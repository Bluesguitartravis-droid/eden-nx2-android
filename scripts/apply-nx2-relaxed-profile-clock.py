#!/usr/bin/env python3
"""The always-on GPU profiler reads the clock without barriers on ARM64.

Nx2Profile::Ticks()/Now() go through WallClock::GetTimeNS, whose ARM64 GetUptime wraps the
CNTVCT_EL0 read in `dsb ish` on both sides. The zones run around every method the GPU back thread
executes (~2.3 million a second in Ocarina) and every macro on the GPU thread, so each one paid two
full barriers twice. On x86 the same profiler already reads the TSC without fences ("those fences
cost ~10% of the GPU back thread").

- WallClock::GetTimeNSRelaxed(): on ARM64 (HAS_NCE) a plain `mrs cntvct_el0` scaled to ns; elsewhere
  GetTimeNS().
- Nx2Profile::Now()/Ticks() use it. EDEN_NX2_PROFILE_FENCED=1 (live, re-read every ~2 s) goes back to
  the fenced read for comparison.
- The GPU profile is off unless EDEN_NX2_PROFILE=1 (read at each game boot, in VideoCore::CreateGPU).
  Profiling was forced on in these test builds, and Maxwell3D turns its fast path (fast_maxwell3d: no
  per-method zones or virtual call, constant buffer words in one write, the front's state blocks) off
  whenever the profile is on. Ocarina at the ranch (Dynarmic, 261): 14.3 fps with it, 15.4 without."""
from pathlib import Path
import sys

src = Path(sys.argv[1]).resolve() / "src"


def replace(path, old, new):
    text = path.read_text()
    if text.count(old) != 1:
        sys.exit(f"relaxed-profile-clock: {path.name}: expected 1 match, found {text.count(old)}:\n{old}")
    path.write_text(text.replace(old, new))


replace(src / "common/cpu_features.h", """    std::chrono::nanoseconds GetTimeNS() const;
""", """    std::chrono::nanoseconds GetTimeNS() const;

    /// NX2: GetTimeNS without the barriers around the counter read (profiling time stamps).
    std::chrono::nanoseconds GetTimeNSRelaxed() const;
""")

cpu = src / "common/cpu_features.cpp"
replace(cpu, """u64 WallClock::NsToTicks(std::chrono::nanoseconds ns) const {
    return MultiplyHigh(ns.count(), cntfrq_ns_factor);
}
#else""", """u64 WallClock::NsToTicks(std::chrono::nanoseconds ns) const {
    return MultiplyHigh(ns.count(), cntfrq_ns_factor);
}

std::chrono::nanoseconds WallClock::GetTimeNSRelaxed() const {
    u64 cntvct_el0 = 0;
    asm volatile("mrs %[cntvct_el0], cntvct_el0" : [cntvct_el0] "=r"(cntvct_el0));
    return std::chrono::nanoseconds{MultiplyHigh(cntvct_el0, ns_cntfrq_factor)};
}
#else""")
replace(cpu, "} // namespace Common\n", """#if defined(ARCHITECTURE_x86_64) || !defined(HAS_NCE)
std::chrono::nanoseconds WallClock::GetTimeNSRelaxed() const {
    return GetTimeNS();
}
#endif

} // namespace Common
""")

profile = src / "video_core/nx2_profile.h"
replace(profile, """inline u64 Now() {
    return static_cast<u64>(Common::g_wall_clock.GetTimeNS().count());
}""", """// NX2: the profiler reads the clock without barriers unless EDEN_NX2_PROFILE_FENCED=1
// (scripts/apply-nx2-relaxed-profile-clock.py).
inline std::atomic<bool> g_fenced_clock{false};

inline u64 Now() {
    if (g_fenced_clock.load(std::memory_order_relaxed)) [[unlikely]] {
        return static_cast<u64>(Common::g_wall_clock.GetTimeNS().count());
    }
    return static_cast<u64>(Common::g_wall_clock.GetTimeNSRelaxed().count());
}""")
replace(profile, """inline void Report(const char* thread_name = "GPU thread", bool report_all_contention = false) {
    if (!Enabled()) {
        return;
    }
    auto& data = t_data;
    const u64 now = Now();""", """inline void Report(const char* thread_name = "GPU thread", bool report_all_contention = false) {
    if (!Enabled()) {
        return;
    }
    auto& data = t_data;
    const u64 now = Now();
    {
        static std::atomic<u64> clock_checked{0};
        const u64 checked = clock_checked.load(std::memory_order_relaxed);
        if (now - checked >= 2'000'000'000ULL &&
            clock_checked.compare_exchange_strong(checked_copy_dummy(checked), now)) {
        }
    }""")
# compare_exchange_strong needs an lvalue; write the block plainly instead.
text = profile.read_text()
text = text.replace("""    {
        static std::atomic<u64> clock_checked{0};
        const u64 checked = clock_checked.load(std::memory_order_relaxed);
        if (now - checked >= 2'000'000'000ULL &&
            clock_checked.compare_exchange_strong(checked_copy_dummy(checked), now)) {
        }
    }""", """    {
        static std::atomic<u64> clock_checked{0};
        u64 checked = clock_checked.load(std::memory_order_relaxed);
        if (now - checked >= 2'000'000'000ULL &&
            clock_checked.compare_exchange_strong(checked, now)) {
            g_fenced_clock.store(VideoCommon::Nx2LiveSwitch("EDEN_NX2_PROFILE_FENCED") == "1",
                                 std::memory_order_relaxed);
        }
    }""")
if '#include "video_core/nx2_live_switch.h"' not in text:
    text = text.replace('#include "video_core/nx2_stutter_trace.h"\n',
                        '#include "video_core/nx2_stutter_trace.h"\n#include "video_core/nx2_live_switch.h"\n', 1)
old = """    g_enabled = true; // Eden NX2 Android test builds: always profile
    StutterTrace::ReadSwitch();"""
if text.count(old) != 1:
    sys.exit("relaxed-profile-clock: ReadSwitch not found once")
text = text.replace(old, """    // NX2: the profile is off unless EDEN_NX2_PROFILE=1: while it is on, Maxwell3D leaves its
    // fast path off (Ocarina at the ranch, Dynarmic, build 261: 14.3 fps on, 15.4 off).
    g_enabled = VideoCommon::Nx2LiveSwitch("EDEN_NX2_PROFILE") == "1";
    StutterTrace::ReadSwitch();""")
profile.write_text(text)
print("relaxed-profile-clock: profiler reads CNTVCT without barriers (EDEN_NX2_PROFILE_FENCED=1 restores)")
