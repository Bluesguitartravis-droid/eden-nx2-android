#!/usr/bin/env python3
"""NX2_HANG: sample the live PC and guest backtrace of threads running on NCE.

Build 95 showed DKB's main thread Runnable for a minute on NCE (no SVCs), but
an NCE thread's saved context is from its last SVC. Before each hang dump the
watchdog interrupts the guest cores; the first time each core leaves guest code
afterwards it logs the thread's live PC and the guest backtrace.
Requires apply-nx2-hang-watchdog.py first.
"""
from pathlib import Path
import sys

root = Path(sys.argv[1])
w = root / "src/common/nx2_watchdog.h"
t = w.read_text()
if "sample_cores" not in t:
    t = t.replace("inline std::atomic<std::int64_t> last_present_ms{0};",
                  "inline std::atomic<std::int64_t> last_present_ms{0};\n"
                  "/// Bit per guest core: log the next guest exit on that core (NX2_HANG sample).\n"
                  "inline std::atomic<std::uint32_t> sample_cores{0};", 1)
    w.write_text(t)

pc = root / "src/core/hle/kernel/physical_core.cpp"
p = pc.read_text()
anchor = """        if (system.DebuggerEnabled()) {
            interface->GetContext(thread->GetContext());
        }
"""
if "NX2_HANG sample" not in p:
    if anchor not in p:
        raise SystemExit("physical_core.cpp: ExitContext shape changed")
    p = p.replace(anchor, anchor + """        if (const u32 bit = 1u << m_core_index;
            (Common::Nx2Watchdog::sample_cores.fetch_and(~bit) & bit) != 0) {
            Kernel::Svc::ThreadContext live{};
            interface->GetContext(live);
            LOG_WARNING(Core, "NX2_HANG sample core {} thread {} pc={:#x} lr={:#x} sp={:#x} x0={:#x} x1={:#x} x2={:#x}",
                        m_core_index, thread->GetThreadId(), live.pc, live.lr, live.sp, live.r[0],
                        live.r[1], live.r[2]);
            interface->LogBacktrace(process);
            // What a spinning thread polls: every register, and 32 bytes at those that point
            // into guest memory.
            auto& memory = process->GetMemory();
            for (u32 r = 0; r < 31; ++r) {
                const u64 value = r < 29 ? live.r[r] : (r == 29 ? live.fp : live.lr);
                std::string words;
                if (value >= 0x1000 && memory.IsValidVirtualAddressRange(value & ~u64{7}, 32)) {
                    for (u64 i = 0; i < 4; ++i) {
                        words += fmt::format(" {:016x}", memory.Read64((value & ~u64{7}) + i * 8));
                    }
                }
                LOG_WARNING(Core, "NX2_HANG sample core {} x{}={:#x}{}{}", m_core_index, r, value,
                            words.empty() ? "" : " ->", words);
            }
            // The code around pc and lr, to name the instruction a spinning thread repeats
            // (an SVC, a counter read, a load-exclusive loop).
            for (const auto [name, at] : {std::pair{"pc", live.pc}, std::pair{"lr", live.lr}}) {
                const u64 base = (at & ~u64{3}) - 32;
                if (at < 0x1000 || !memory.IsValidVirtualAddressRange(base, 64)) {
                    continue;
                }
                std::string code;
                for (u64 i = 0; i < 16; ++i) {
                    code += fmt::format(" {:08x}", memory.Read32(base + i * 4));
                }
                LOG_WARNING(Core, "NX2_HANG sample core {} code {}-32:{}", m_core_index, name, code);
            }
        }
""", 1)
    p = p.replace('#include "common/settings.h"\n', '#include "common/settings.h"\n#include "common/nx2_watchdog.h"\n#include "common/logging.h"\n#include "core/memory.h"\n#include <string>\n#include <utility>\n', 1)
    pc.write_text(p)

core = root / "src/core/core.cpp"
c = core.read_text()
hook = """                    LOG_WARNING(Core, "NX2_HANG no present for {} ms (dump {}/6, nce={})", idle, dumps,
                                Settings::IsNceEnabled());
"""
if "sample_cores.store" not in c:
    if hook not in c:
        raise SystemExit("core.cpp: NX2_HANG watchdog not found (apply-nx2-hang-watchdog.py first)")
    c = c.replace(hook, hook + """                    Common::Nx2Watchdog::sample_cores.store(0xF);
                    for (std::size_t core_id = 0; core_id < 4; ++core_id) {
                        system.Kernel().PhysicalCore(core_id).Interrupt();
                    }
                    std::this_thread::sleep_for(std::chrono::milliseconds(200));
                    Common::Nx2Watchdog::sample_cores.store(0);
""", 1)
    core.write_text(c)
print("NX2_HANG live-PC sampling installed")
