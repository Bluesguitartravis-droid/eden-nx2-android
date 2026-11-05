#!/usr/bin/env python3
"""Log where guest threads are when a game stops presenting (NX2_HANG).

On NCE, DKB and Ocarina of Time stop right after creating their GPU channel:
no GPU work, no further service calls, no crash. When no frame has been
presented for 8 s, dump each guest thread's state, wait reason and saved
PC/LR (a thread in an SVC wait has its context saved), at most 6 times.
"""
from pathlib import Path
import sys

root = Path(sys.argv[1])
(root / "src/common/nx2_watchdog.h").write_text('''// Eden NX2 Android: last-present timestamp for the hang watchdog in core.cpp.
#pragma once

#include <atomic>
#include <chrono>
#include <cstdint>

namespace Common::Nx2Watchdog {
inline std::atomic<std::int64_t> last_present_ms{0};
inline std::int64_t NowMs() {
    return std::chrono::duration_cast<std::chrono::milliseconds>(
               std::chrono::steady_clock::now().time_since_epoch())
        .count();
}
} // namespace Common::Nx2Watchdog
''')

gsv = root / "src/core/hle/service/nx2_gsv.h"
g = gsv.read_text()
anchor = "    void PresentClientBuffer(HLERequestContext& ctx) {\n        const auto* w = ctx.CommandBuffer();\n"
if "Nx2Watchdog" not in g:
    if anchor not in g:
        raise SystemExit("nx2_gsv.h: PresentClientBuffer shape changed")
    g = g.replace(anchor, anchor + "        Common::Nx2Watchdog::last_present_ms.store(Common::Nx2Watchdog::NowMs());\n", 1)
    g = g.replace("#pragma once\n", '#pragma once\n\n#include "common/nx2_watchdog.h"\n', 1)
    gsv.write_text(g)

core = root / "src/core/core.cpp"
c = core.read_text()
hook = "        Settings::SetCurrentProgramID(params.program_id);\n"
if "NX2_HANG" not in c:
    if hook not in c:
        raise SystemExit("core.cpp: SetCurrentProgramID hook missing")
    c = c.replace(hook, hook + r'''
        // Eden NX2 Android: dump guest threads when presentation stops (NX2_HANG).
        {
            static std::atomic<u64> nx2_watch_generation{0};
            const u64 generation = ++nx2_watch_generation;
            Common::Nx2Watchdog::last_present_ms.store(Common::Nx2Watchdog::NowMs());
            const s64 started = Common::Nx2Watchdog::NowMs();
            std::thread([&system, generation, started] {
                int dumps = 0;
                s64 last_dump = 0;
                while (generation == nx2_watch_generation.load() && dumps < 6) {
                    std::this_thread::sleep_for(std::chrono::seconds(2));
                    if (generation != nx2_watch_generation.load() || system.IsShuttingDown()) {
                        return;
                    }
                    const s64 now = Common::Nx2Watchdog::NowMs();
                    const s64 idle = now - Common::Nx2Watchdog::last_present_ms.load();
                    if (now - started < 10000 || idle < 8000 || now - last_dump < 10000) {
                        continue;
                    }
                    auto* process = system.ApplicationProcess();
                    if (process == nullptr) {
                        continue;
                    }
                    last_dump = now;
                    ++dumps;
                    LOG_WARNING(Core, "NX2_HANG no present for {} ms (dump {}/6, nce={})", idle, dumps,
                                Settings::IsNceEnabled());
                    for (const auto& thread : process->GetThreadList()) {
                        const auto& tc = thread.GetContext();
                        LOG_WARNING(Core,
                                    "NX2_HANG thread {} core={} prio={} state={:#x} wait={} "
                                    "pc={:#x} lr={:#x} sp={:#x} x0={:#x} x1={:#x}",
                                    thread.GetThreadId(), thread.GetActiveCore(),
                                    thread.GetPriority(), static_cast<u32>(thread.GetState()),
                                    static_cast<u32>(thread.GetWaitReasonForDebugging()), tc.pc,
                                    tc.lr, tc.sp, tc.r[0], tc.r[1]);
                    }
                }
            }).detach();
        }
''', 1)
    c = c.replace('#include "core/hle/kernel/k_process.h"\n',
                  '#include "core/hle/kernel/k_process.h"\n#include "core/hle/kernel/k_thread.h"\n#include "common/nx2_watchdog.h"\n#include <thread>\n', 1)
    core.write_text(c)
print("NX2_HANG watchdog installed")
