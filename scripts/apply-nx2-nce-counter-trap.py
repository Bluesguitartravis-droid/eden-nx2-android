#!/usr/bin/env python3
"""NCE: emulate counter reads the patcher missed, when they trap as undefined instructions.

Build 247 named the instruction that halted Ocarina's NCE boot: d53be02b, `MRS X11, CNTPCT_EL0`,
once a frame. The patcher rewrites the counter reads it finds in a module's text, but not this
one, and EL0 reads of CNTPCT_EL0 trap (SIGILL) on the Odin 3's kernel. Skipping it (247's
EDEN_NX2_NCE_SKIP_UNDEFINED) leaves a stale register and the game sat on a black screen.

Now the SIGILL handler recognises MRS Xt of CNTPCT_EL0, CNTVCT_EL0 or CNTFRQ_EL0, writes the guest
counter (host CNTVCT_EL0 scaled by the guest CNTFRQ factor, as Patcher::WriteCounterRead does)
or the guest frequency to Xt, and resumes at the next instruction. Counted in the NCE report as
`counter reads emulated`."""
from pathlib import Path
import sys

src = Path(sys.argv[1]).resolve() / "src"


def replace(path, old, new):
    text = path.read_text()
    if text.count(old) != 1:
        sys.exit(f"nce-counter-trap: {path.name}: expected 1 match, found {text.count(old)}:\n{old}")
    path.write_text(text.replace(old, new))


stats = src / "common/nx2_nce_stats.h"
replace(stats, "inline std::atomic<bool> skip_undefined{};\n",
        "inline std::atomic<bool> skip_undefined{};\n"
        "// Counter reads (MRS CNTPCT/CNTVCT/CNTFRQ) that trapped and were emulated.\n"
        "inline std::atomic<std::uint64_t> counter_traps{};\n")

nce = src / "core/arm/nce/arm_nce.cpp"
replace(nce, """    // NX2: name it (scripts/apply-nx2-nce-undefined.py)
""", """    // NX2: a counter read the patcher missed (scripts/apply-nx2-nce-counter-trap.py)
    {
        auto& nx2_ctx = static_cast<ucontext_t*>(raw_context)->uc_mcontext;
        const u32 word = *reinterpret_cast<const u32*>(nx2_ctx.pc);
        const u32 rt = word & 0x1fU;
        const u32 reg = word & ~0x1fU;
        constexpr u32 MrsCntfrq = 0xd53be000U;
        constexpr u32 MrsCntpct = 0xd53be020U;
        constexpr u32 MrsCntvct = 0xd53be040U;
        if (reg == MrsCntfrq || reg == MrsCntpct || reg == MrsCntvct) {
            u64 value;
            if (reg == MrsCntfrq) {
                value = Common::g_wall_clock.GetGuestCNTFRQ();
            } else {
                u64 host;
                asm volatile("mrs %0, cntvct_el0" : "=r"(host));
                const auto factor = Common::g_wall_clock.GetGuestCNTFRQFactor();
                const u64 factor_lo = static_cast<u64>(factor);
                const u64 factor_hi = static_cast<u64>(factor >> 64);
                value = static_cast<u64>(
                            (static_cast<unsigned __int128>(host) * factor_lo) >> 64) +
                        host * factor_hi;
            }
            if (rt != 31) {
                nx2_ctx.regs[rt] = value;
            }
            nx2_ctx.pc += 4;
            Common::Nx2NceStats::counter_traps.fetch_add(1, std::memory_order_relaxed);
            return true;
        }
    }
    // NX2: name it (scripts/apply-nx2-nce-undefined.py)
""")
text = nce.read_text()
if '#include "common/cpu_features.h"' not in text:
    nce.write_text(text.replace('#include "common/settings.h"\n',
                                '#include "common/cpu_features.h"\n#include "common/settings.h"\n', 1))

core = src / "core/core.cpp"
replace(core, """                                    if (const u64 nx2_udf =
                                            Common::Nx2NceStats::undefined_count.exchange(0)) {""",
        """                                    if (const u64 nx2_ctr =
                                            Common::Nx2NceStats::counter_traps.exchange(0)) {
                                        LOG_INFO(Core, "NX2_NCE counter reads emulated {} in 10 s",
                                                 nx2_ctr);
                                    }
                                    if (const u64 nx2_udf =
                                            Common::Nx2NceStats::undefined_count.exchange(0)) {""")
print("nce-counter-trap: trapped counter reads are emulated")
