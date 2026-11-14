#!/usr/bin/env python3
"""Diagnostics that answer the questions the logs could not (builds 131-143):

- NX2_FRAMES, every 10 s: frames per second and the present-to-present time's median, 95th and
  99th percentile and maximum, for Switch 1 and Switch 2 games alike. Average FPS hides stutter;
  the percentiles show how even the frames are.
- NX2_STOP: who ended the game. The frontend's request is logged with the Kotlin call path, the
  emulated system's own exit (System::Exit) with the native callers, and the emulation
  activity's pause/resume/destroy are logged. Before, a game that "closed after a minute" left
  nothing in the log.
- NX2_SUMMARY, when the game stops: one block with the build, game, driver, CPU backend, session
  length, frame statistics and the unimplemented service commands the game used (each once).
- NX2_SESSION at start: the wall-clock time, to line the log up with an Android bug report, and
  whether Android's performance hint sessions are available.
- The hang detector used only the Switch 2 present path, so it reported "no present" for every
  Switch 1 game, and after six reports the thread that also writes NX2_THREADS / NX2_NCE /
  NX2_GPU stopped. It now follows PerfStats::EndSystemFrame (both paths), samples all six guest
  cores, and keeps reporting after its last hang dump.

Also: Switch 2 presents now report their frame interval to Android's performance hint session
(ADPF), as Switch 1 presents already do, so the system raises the emulator threads' CPU frequency
when frames run late. EDEN_NX2_ADPF=0 turns the reports off."""
from pathlib import Path
import sys

root = Path(sys.argv[1]).resolve() / "src"


def patch(rel, old, new, marker, required=True):
    p = root / rel
    t = p.read_text()
    if marker in t:
        return True
    if old not in t:
        if required:
            raise SystemExit(f"{rel}: anchor not found: {old[:80]!r}")
        print(f"NX2_PATCH_SKIP: {rel}: {old[:60]!r}")
        return False
    p.write_text(t.replace(old, new, 1))
    return True


def include(rel, line):
    p = root / rel
    t = p.read_text()
    if line not in t:
        p.write_text(line + t)


(root / "common/nx2_session.h").write_text(r"""// Eden NX2 Android diagnostics shared between threads: frame pacing statistics and the
// unimplemented service commands a game used (apply-nx2-diagnostics.py).
#pragma once

#include <algorithm>
#include <array>
#include <atomic>
#include <chrono>
#include <cstddef>
#include <cstdint>
#include <map>
#include <mutex>
#include <string>
#include <utility>
#include <vector>

#include <fmt/format.h>

namespace Common::Nx2Session {

[[nodiscard]] inline std::int64_t NowUs() {
    return std::chrono::duration_cast<std::chrono::microseconds>(
               std::chrono::steady_clock::now().time_since_epoch())
        .count();
}

/// Present-to-present times in 1 ms buckets; the last bucket holds everything longer.
struct FrameWindow {
    static constexpr std::size_t Buckets = 400;
    std::array<std::atomic<std::uint32_t>, Buckets> buckets{};
    std::atomic<std::uint64_t> total_us{};
    std::atomic<std::uint64_t> max_us{};

    void Add(std::uint64_t us) {
        buckets[(std::min)(static_cast<std::size_t>(us / 1000), Buckets - 1)].fetch_add(
            1, std::memory_order_relaxed);
        total_us.fetch_add(us, std::memory_order_relaxed);
        std::uint64_t seen = max_us.load(std::memory_order_relaxed);
        while (us > seen && !max_us.compare_exchange_weak(seen, us, std::memory_order_relaxed)) {
        }
    }

    /// "18.7 fps, 187 frames, frame ms p50 52 p95 71 p99 104 max 180, 3 over twice the median";
    /// empty when no frame was presented.
    [[nodiscard]] std::string Describe(bool reset) {
        std::array<std::uint32_t, Buckets> counts{};
        std::uint64_t frames = 0;
        for (std::size_t i = 0; i < Buckets; ++i) {
            counts[i] = reset ? buckets[i].exchange(0, std::memory_order_relaxed)
                              : buckets[i].load(std::memory_order_relaxed);
            frames += counts[i];
        }
        const std::uint64_t total = reset ? total_us.exchange(0, std::memory_order_relaxed)
                                          : total_us.load(std::memory_order_relaxed);
        const std::uint64_t longest = reset ? max_us.exchange(0, std::memory_order_relaxed)
                                            : max_us.load(std::memory_order_relaxed);
        if (frames == 0 || total == 0) {
            return {};
        }
        const auto percentile = [&](double fraction) {
            const std::uint64_t target =
                (std::max<std::uint64_t>)(1, static_cast<std::uint64_t>(frames * fraction + 0.5));
            std::uint64_t seen = 0;
            for (std::size_t i = 0; i < Buckets; ++i) {
                seen += counts[i];
                if (seen >= target) {
                    return i;
                }
            }
            return Buckets - 1;
        };
        const std::size_t median = percentile(0.5);
        std::uint64_t slow = 0;
        for (std::size_t i = (std::min)(median * 2 + 1, Buckets - 1); i < Buckets; ++i) {
            slow += counts[i];
        }
        return fmt::format("{:.1f} fps, {} frames, frame ms p50 {} p95 {} p99 {} max {}, "
                           "{} over twice the median",
                           static_cast<double>(frames) * 1e6 / static_cast<double>(total), frames,
                           median, percentile(0.95), percentile(0.99), longest / 1000, slow);
    }
};

inline FrameWindow recent;  ///< Since the last report
inline FrameWindow session; ///< Since the game started
inline std::atomic<std::int64_t> last_present_us{0};
inline std::atomic<std::int64_t> session_start_us{0};

inline std::mutex unimplemented_mutex;
inline std::map<std::string, std::uint32_t> unimplemented;

/// A game starts: forget the last one's statistics.
inline void Start() {
    (void)recent.Describe(true);
    (void)session.Describe(true);
    last_present_us.store(0, std::memory_order_relaxed);
    session_start_us.store(NowUs(), std::memory_order_relaxed);
    std::scoped_lock lock{unimplemented_mutex};
    unimplemented.clear();
}

/// A frame was presented (PerfStats::EndSystemFrame: Switch 1 and Switch 2).
inline void OnPresent() {
    const std::int64_t now = NowUs();
    const std::int64_t previous = last_present_us.exchange(now, std::memory_order_relaxed);
    if (previous == 0 || now <= previous) {
        return;
    }
    const auto us = static_cast<std::uint64_t>(now - previous);
    recent.Add(us);
    session.Add(us);
}

inline void NoteUnimplemented(std::string command) {
    std::scoped_lock lock{unimplemented_mutex};
    if (unimplemented.size() < 256 || unimplemented.contains(command)) {
        ++unimplemented[std::move(command)];
    }
}

/// "nx2:chat:u 3 (x2), nx2:vi2 53", most used first; "none" when the game used none.
[[nodiscard]] inline std::string DescribeUnimplemented(std::size_t limit) {
    std::vector<std::pair<std::string, std::uint32_t>> list;
    {
        std::scoped_lock lock{unimplemented_mutex};
        list.assign(unimplemented.begin(), unimplemented.end());
    }
    if (list.empty()) {
        return "none";
    }
    std::stable_sort(list.begin(), list.end(),
                     [](const auto& a, const auto& b) { return a.second > b.second; });
    std::string out;
    for (std::size_t i = 0; i < list.size() && i < limit; ++i) {
        out += fmt::format("{}{}", i == 0 ? "" : ", ", list[i].first);
        if (list[i].second > 1) {
            out += fmt::format(" (x{})", list[i].second);
        }
    }
    if (list.size() > limit) {
        out += fmt::format(", and {} more", list.size() - limit);
    }
    return out;
}

} // namespace Common::Nx2Session
""")

# Every presented frame, Switch 1 and Switch 2.
has_watchdog = (root / "common/nx2_watchdog.h").exists()
patch("core/perf_stats.cpp",
      """void PerfStats::EndSystemFrame() {
    std::scoped_lock lock{object_mutex};
""",
      """void PerfStats::EndSystemFrame() {
    Common::Nx2Session::OnPresent();
""" + ("""    Common::Nx2Watchdog::last_present_ms.store(Common::Nx2Watchdog::NowMs());
""" if has_watchdog else "") + """    std::scoped_lock lock{object_mutex};
""",
      "Common::Nx2Session::OnPresent();")
include("core/perf_stats.cpp", '#include "common/nx2_session.h"\n')
if has_watchdog:
    include("core/perf_stats.cpp", '#include "common/nx2_watchdog.h"\n')

# Switch 2 presents feed Android's performance hint session, as Switch 1 presents do.
if patch("core/hle/service/nx2_gsv.h",
         """        system.GPU().RequestComposite(std::move(layers), std::move(fences));
        CaptureFrame();
""",
         """        system.GPU().RequestComposite(std::move(layers), std::move(fences));
        {
            // NX2 Android: as nvdisp_disp0 does for Switch 1 presents (EDEN_NX2_ADPF=0: off).
            static const bool nx2_adpf = [] {
                const char* value = std::getenv("EDEN_NX2_ADPF");
                return value == nullptr || value[0] != '0';
            }();
            if (nx2_adpf) {
                Common::ADPF::ReportFrameInterval();
            }
        }
        CaptureFrame();
""",
         "Common::ADPF::ReportFrameInterval();", required=False):
    include("core/hle/service/nx2_gsv.h", '#include <cstdlib>\n#include "common/adpf.h"\n')

# Unimplemented service commands, each once in the summary.
if patch("core/hle/service/service.cpp",
         "    system.GetReporter().SaveUnimplementedFunctionReport(ctx, ctx.GetCommand(), function_name, service_name);\n",
         "    Common::Nx2Session::NoteUnimplemented(fmt::format(\"{} {}\", service_name, ctx.GetCommand()));\n"
         "    system.GetReporter().SaveUnimplementedFunctionReport(ctx, ctx.GetCommand(), function_name, service_name);\n",
         "Nx2Session::NoteUnimplemented", required=False):
    include("core/hle/service/service.cpp", '#include "common/nx2_session.h"\n')

# The reporter thread (apply-nx2-hang-watchdog.py and later scripts).
core = root / "core/core.cpp"
c = core.read_text()
if "NX2_FRAMES" not in c:
    edits = [
        # Keep the periodic lines after the last hang dump.
        ("                while (generation == nx2_watch_generation.load() && dumps < 6) {\n",
         "                while (generation == nx2_watch_generation.load()) {\n"),
        ("                    if (now - started < 10000 || idle < 8000 || now - last_dump < 10000) {\n",
         "                    if (dumps >= 6 || now - started < 10000 || idle < 8000 ||\n"
         "                        now - last_dump < 10000) {\n"),
        # All guest cores.
        ("                    Common::Nx2Watchdog::sample_cores.store(0xF);\n"
         "                    for (std::size_t core_id = 0; core_id < 4; ++core_id) {\n",
         "                    Common::Nx2Watchdog::sample_cores.store(\n"
         "                        (1U << Core::Hardware::NUM_CPU_CORES) - 1);\n"
         "                    for (std::size_t core_id = 0; core_id < Core::Hardware::NUM_CPU_CORES;\n"
         "                         ++core_id) {\n"),
        ("                            LOG_INFO(Core, \"NX2_GPU busy={} clk={} max={} temp={} switches=[{}]\",\n",
         "                            if (const std::string nx2_frames =\n"
         "                                    Common::Nx2Session::recent.Describe(true);\n"
         "                                !nx2_frames.empty()) {\n"
         "                                LOG_INFO(Core, \"NX2_FRAMES {}\", nx2_frames);\n"
         "                            } else {\n"
         "                                LOG_INFO(Core, \"NX2_FRAMES no frame presented in this window\");\n"
         "                            }\n"
         "                            LOG_INFO(Core, \"NX2_GPU busy={} clk={} max={} temp={} switches=[{}]\",\n"),
        # Session start.
        ("            Common::Nx2Watchdog::last_present_ms.store(Common::Nx2Watchdog::NowMs());\n"
         "            const s64 started = Common::Nx2Watchdog::NowMs();\n",
         "            Common::Nx2Watchdog::last_present_ms.store(Common::Nx2Watchdog::NowMs());\n"
         "            const s64 started = Common::Nx2Watchdog::NowMs();\n"
         "            Common::Nx2Session::Start();\n"
         "            {\n"
         "                const std::time_t nx2_wall = std::time(nullptr);\n"
         "                std::tm nx2_tm{};\n"
         "                localtime_r(&nx2_wall, &nx2_tm);\n"
         "                char nx2_stamp[40]{};\n"
         "                std::strftime(nx2_stamp, sizeof(nx2_stamp), \"%Y-%m-%d %H:%M:%S %z\", &nx2_tm);\n"
         "                LOG_INFO(Core, \"NX2_SESSION start wall={} program={:016X} nce={} \"\n"
         "                               \"performance hints={}\",\n"
         "                         nx2_stamp, params.program_id, Settings::IsNceEnabled(),\n"
         "                         Common::ADPF::IsSessionSupported(Common::ADPF::Session::Render));\n"
         "            }\n"),
    ]
    for old, new in edits:
        if old not in c:
            raise SystemExit(f"core.cpp: reporter anchor not found: {old[:70]!r}")
        c = c.replace(old, new, 1)
    core.write_text(c)
for line in ('#include "common/nx2_session.h"\n', '#include "common/adpf.h"\n', "#include <ctime>\n",
             '#include "core/hardware_properties.h"\n'):
    include("core/core.cpp", line)

# The emulated system's own exit, with its native callers.
patch("core/core.cpp",
      """void System::Exit() {
    if (impl->exit_callback) {
""",
      """#ifdef __ANDROID__
namespace {
struct Nx2CallerTrace {
    std::array<uintptr_t, 14> pcs{};
    std::size_t count{};
};
_Unwind_Reason_Code Nx2CallerFrame(_Unwind_Context* context, void* argument) {
    auto* trace = static_cast<Nx2CallerTrace*>(argument);
    if (trace->count == trace->pcs.size()) {
        return _URC_END_OF_STACK;
    }
    trace->pcs[trace->count++] = static_cast<uintptr_t>(_Unwind_GetIP(context));
    return _URC_NO_REASON;
}
std::string Nx2Callers() {
    Nx2CallerTrace trace;
    _Unwind_Backtrace(Nx2CallerFrame, &trace);
    std::string out;
    for (std::size_t i = 2; i < trace.count; ++i) {
        Dl_info info{};
        if (dladdr(reinterpret_cast<void*>(trace.pcs[i]), &info) != 0 && info.dli_sname) {
            out += fmt::format(" <- {}", info.dli_sname);
        } else {
            out += fmt::format(" <- {:#x}",
                               trace.pcs[i] - reinterpret_cast<uintptr_t>(info.dli_fbase));
        }
    }
    return out;
}
} // Anonymous namespace
#endif

void System::Exit() {
#ifdef __ANDROID__
    LOG_WARNING(Core, "NX2_STOP the emulated system asked to exit{}", Nx2Callers());
#endif
    if (impl->exit_callback) {
""",
      "NX2_STOP the emulated system asked to exit")
c = core.read_text()
if "#include <unwind.h>" not in c:
    core.write_text("#ifdef __ANDROID__\n#include <dlfcn.h>\n#include <unwind.h>\n#endif\n"
                    "#include <cstdint>\n#include <string>\n" + c)

# The frontend's stop request and the summary.
native = "android/app/src/main/jni/native.cpp"
patch(native,
      """void Java_io_github_zortos293_lichii_NativeLibrary_stopEmulation(JNIEnv* env, jclass clazz) {
    EmulationSession::GetInstance().HaltEmulation();
""",
      """void Java_io_github_zortos293_lichii_NativeLibrary_stopEmulation(JNIEnv* env, jclass clazz) {
    LOG_INFO(Frontend, "NX2_STOP the frontend asked to stop");
    EmulationSession::GetInstance().HaltEmulation();
""",
      "NX2_STOP the frontend asked to stop")
patch(native,
      """    // Shutdown the main emulated process
    if (m_load_result == Core::SystemResultStatus::Success) {
""",
      """    // Shutdown the main emulated process
    if (m_load_result == Core::SystemResultStatus::Success) {
        {
            const s64 nx2_started = Common::Nx2Session::session_start_us.load();
            const std::string nx2_frames = Common::Nx2Session::session.Describe(false);
            LOG_INFO(Frontend,
                     "NX2_SUMMARY build={}-{} program={:016X} cpu={} driver=\\"{}\\" session={} s | "
                     "frames: {} | unimplemented service commands: {}",
                     Common::g_scm_branch, Common::g_scm_desc,
                     m_system.GetApplicationProcessProgramID(),
                     Settings::IsNceEnabled() ? "NCE" : "Dynarmic",
                     m_system.Renderer().GetDeviceVendor(),
                     nx2_started != 0 ? (Common::Nx2Session::NowUs() - nx2_started) / 1000000 : 0,
                     nx2_frames.empty() ? "none presented" : nx2_frames,
                     Common::Nx2Session::DescribeUnimplemented(24));
        }
""",
      "NX2_SUMMARY build=")
include(native, '#include "common/nx2_session.h"\n')

# Kotlin: who asked for the stop, and the activity's lifecycle.
kt = root / "android/app/src/main/java/io/github/zortos293/lichii"
lib = kt / "NativeLibrary.kt"
t = lib.read_text()
if "fun nx2StopEmulation" not in t:
    old = "    external fun stopEmulation()\n"
    if old not in t:
        raise SystemExit("NativeLibrary.kt: stopEmulation not found")
    t = t.replace(old, old + """
    /** NX2: stopEmulation, with the Kotlin call path in the log (who asked for the stop). */
    fun nx2StopEmulation() {
        val path = Throwable().stackTrace.drop(1).take(7).joinToString(" <- ") {
            it.className.substringAfterLast('.') + "." + it.methodName + ":" + it.lineNumber
        }
        io.github.zortos293.lichii.utils.Log.info("[NX2_STOP] stop requested by " + path)
        stopEmulation()
    }
""", 1)
    lib.write_text(t)
for rel in ("activities/EmulationActivity.kt", "fragments/EmulationFragment.kt"):
    p = kt / rel
    t = p.read_text()
    if "NativeLibrary.nx2StopEmulation()" not in t:
        if "NativeLibrary.stopEmulation()" not in t:
            raise SystemExit(f"{rel}: no stopEmulation call")
        p.write_text(t.replace("NativeLibrary.stopEmulation()", "NativeLibrary.nx2StopEmulation()"))
act = kt / "activities/EmulationActivity.kt"
t = act.read_text()
if "[NX2_LIFECYCLE]" not in t:
    for old, new in [
        ("    override fun onResume() {\n        super.onResume()\n",
         "    override fun onResume() {\n        super.onResume()\n"
         "        Log.info(\"[NX2_LIFECYCLE] EmulationActivity resumed\")\n"),
        ("    override fun onPause() {\n",
         "    override fun onPause() {\n"
         "        Log.info(\"[NX2_LIFECYCLE] EmulationActivity paused (finishing=$isFinishing)\")\n"),
        ("    override fun onDestroy() {\n",
         "    override fun onDestroy() {\n"
         "        Log.info(\"[NX2_LIFECYCLE] EmulationActivity destroyed (finishing=$isFinishing)\")\n"),
        ("    fun onEmulationStopped(status: Int) {\n",
         "    fun onEmulationStopped(status: Int) {\n"
         "        Log.info(\"[NX2_STOP] emulation stopped with status $status\")\n"),
    ]:
        if old not in t:
            raise SystemExit(f"EmulationActivity.kt: anchor not found: {old[:50]!r}")
        t = t.replace(old, new, 1)
    act.write_text(t)
print("NX2 diagnostics: frames, stop reasons, summary, lifecycle; Switch 2 presents report to ADPF")
