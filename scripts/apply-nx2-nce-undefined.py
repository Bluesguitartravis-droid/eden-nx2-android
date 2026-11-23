#!/usr/bin/env python3
"""NCE: name the undefined instruction that halts a guest thread, and optionally skip it.

Ocarina never finished booting on NCE (builds 226, 244, 246): at ~11.5 s one guest thread took
SIGILL at Owl.nss+0x3C4620C, HandleGuestIllegalInstruction halted it as a prefetch abort
(debug knob 2, which skips instead, is not reachable from the Lab), and the boot waited on it
forever. Dynarmic runs the same code.

- Every undefined instruction is counted with its word and pc; the 10 s NCE report adds a line
  `NX2_NCE undefined instructions <n> in 10 s, last <word> at <pc>`.
- EDEN_NX2_NCE_SKIP_UNDEFINED=1 in the switches file (read at each game boot) skips them as
  knob 2 does."""
from pathlib import Path
import sys

src = Path(sys.argv[1]).resolve() / "src"


def replace(path, old, new):
    text = path.read_text()
    if text.count(old) != 1:
        sys.exit(f"nce-undefined: {path.name}: expected 1 match, found {text.count(old)}:\n{old}")
    path.write_text(text.replace(old, new))


stats = src / "common/nx2_nce_stats.h"
replace(stats, "} // namespace Common::Nx2NceStats", """// Undefined instructions in guest code (SIGILL): count, the last one's word and pc, and
// whether to skip them (EDEN_NX2_NCE_SKIP_UNDEFINED, set at each game boot).
inline std::atomic<std::uint64_t> undefined_count{};
inline std::atomic<std::uint64_t> undefined_pc{};
inline std::atomic<std::uint32_t> undefined_word{};
inline std::atomic<bool> skip_undefined{};
} // namespace Common::Nx2NceStats""")

nce = src / "core/arm/nce/arm_nce.cpp"
replace(nce, """    // knob2 (debug_knobs bit 2, value 4): skip undefined instructions in guest code instead of
    // halting the thread, like Dynarmic, which logs them and carries on.
    if (Settings::GetDebugKnobAt(2)) {""", """    // NX2: name it (scripts/apply-nx2-nce-undefined.py)
    {
        auto& nx2_ctx = static_cast<ucontext_t*>(raw_context)->uc_mcontext;
        Common::Nx2NceStats::undefined_count.fetch_add(1, std::memory_order_relaxed);
        Common::Nx2NceStats::undefined_pc.store(nx2_ctx.pc, std::memory_order_relaxed);
        Common::Nx2NceStats::undefined_word.store(*reinterpret_cast<const u32*>(nx2_ctx.pc),
                                                  std::memory_order_relaxed);
    }
    // knob2 (debug_knobs bit 2, value 4): skip undefined instructions in guest code instead of
    // halting the thread, like Dynarmic, which logs them and carries on.
    if (Settings::GetDebugKnobAt(2) ||
        Common::Nx2NceStats::skip_undefined.load(std::memory_order_relaxed)) {""")

core = src / "core/core.cpp"
replace(core, """                                             Common::Nx2NceWrites::not_emulated.exchange(0) / window_s);
""", """                                             Common::Nx2NceWrites::not_emulated.exchange(0) / window_s);
                                    if (const u64 nx2_udf =
                                            Common::Nx2NceStats::undefined_count.exchange(0)) {
                                        LOG_WARNING(Core,
                                                    "NX2_NCE undefined instructions {} in 10 s, "
                                                    "last {:08x} at {:#x} (skip={})",
                                                    nx2_udf, Common::Nx2NceStats::undefined_word.load(),
                                                    Common::Nx2NceStats::undefined_pc.load(),
                                                    Common::Nx2NceStats::skip_undefined.load());
                                    }
""")

dm = src / "core/device_memory.cpp"
replace(dm, """    const bool requested = Settings::values.cpu_backend.GetValue() == Settings::CpuBackend::Nce;
""", """    Common::Nx2NceStats::skip_undefined.store(
        VideoCommon::Nx2LiveSwitch("EDEN_NX2_NCE_SKIP_UNDEFINED") == "1");
    const bool requested = Settings::values.cpu_backend.GetValue() == Settings::CpuBackend::Nce;
""")
text = dm.read_text()
if '#include "common/nx2_nce_stats.h"' not in text:
    dm.write_text(text.replace('#include "core/device_memory.h"\n',
                               '#include "core/device_memory.h"\n#include "common/nx2_nce_stats.h"\n', 1))
text = nce.read_text()
if '#include "common/nx2_nce_stats.h"' not in text:
    sys.exit("nce-undefined: arm_nce.cpp does not include nx2_nce_stats.h")
print("nce-undefined: undefined instructions named; EDEN_NX2_NCE_SKIP_UNDEFINED added")
