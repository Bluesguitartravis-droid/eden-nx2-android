#!/usr/bin/env python3
from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: apply-nx2-null-branch-diagnostics.py <upstream-dir>")

root = Path(sys.argv[1])
path = root / "src" / "core" / "arm" / "dynarmic" / "arm_dynarmic_64.cpp"
text = path.read_text()

old = """    case Dynarmic::A64::Exception::NoExecuteFault:
        LOG_CRITICAL(Core_ARM, "Cannot execute instruction at unmapped address {:#016x}", pc);
        ReturnException(pc, PrefetchAbort);
        return;
"""

new = """    case Dynarmic::A64::Exception::NoExecuteFault: {
        LOG_CRITICAL(Core_ARM, "Cannot execute instruction at unmapped address {:#016x}", pc);

        // NX2 bring-up diagnostic: a recurring Switch 2 failure reaches PC 0x8/0xC
        // after an indirect guest branch. Decode the call site and dump enough
        // object state to identify the service/interface that supplied the bad target.
        Kernel::Svc::ThreadContext ctx{};
        m_parent.GetContext(ctx);

        const u64 lr = ctx.lr;
        const u64 callsite = lr >= 4 ? lr - 4 : 0;
        const auto reg_value = [&](u32 index) -> u64 {
            if (index < 29) {
                return ctx.r[index];
            }
            if (index == 29) {
                return ctx.fp;
            }
            if (index == 30) {
                return ctx.lr;
            }
            return 0;
        };

        LOG_CRITICAL(
            Core_ARM,
            "NX2_BRANCH_FAULT pc={:#018x} lr={:#018x} sp={:#018x} fp={:#018x}",
            pc, lr, ctx.sp, ctx.fp);

        if (callsite != 0 && m_memory.IsValidVirtualAddressRange(callsite, 4)) {
            const u32 insn = m_memory.Read32(callsite);
            const u32 rn = (insn >> 5) & 0x1f;
            const u64 target = reg_value(rn);

            if ((insn & 0xFFFFFC1Fu) == 0xD63F0000u) {
                LOG_CRITICAL(
                    Core_ARM,
                    "NX2_BRANCH_DECODE callsite={:#018x} insn={:#010x} BLR X{} target={:#018x}",
                    callsite, insn, rn, target);
            } else if ((insn & 0xFFFFFC1Fu) == 0xD61F0000u) {
                LOG_CRITICAL(
                    Core_ARM,
                    "NX2_BRANCH_DECODE callsite={:#018x} insn={:#010x} BR X{} target={:#018x}",
                    callsite, insn, rn, target);
            } else {
                LOG_CRITICAL(
                    Core_ARM,
                    "NX2_BRANCH_DECODE callsite={:#018x} insn={:#010x} indirect=unknown",
                    callsite, insn);
            }

            for (s64 delta = -16; delta <= 8; delta += 4) {
                const u64 addr = static_cast<u64>(static_cast<s64>(callsite) + delta);
                if (m_memory.IsValidVirtualAddressRange(addr, 4)) {
                    LOG_CRITICAL(
                        Core_ARM,
                        "NX2_BRANCH_CODE addr={:#018x} delta={:+d} insn={:#010x}",
                        addr, delta, m_memory.Read32(addr));
                }
            }
        } else {
            LOG_CRITICAL(Core_ARM, "NX2_BRANCH_DECODE callsite unavailable");
        }

        LOG_CRITICAL(
            Core_ARM,
            "NX2_BRANCH_REGS x0={:#018x} x1={:#018x} x2={:#018x} x3={:#018x} "
            "x8={:#018x} x16={:#018x} x17={:#018x} x19={:#018x}",
            ctx.r[0], ctx.r[1], ctx.r[2], ctx.r[3], ctx.r[8], ctx.r[16], ctx.r[17], ctx.r[19]);

        LOG_CRITICAL(
            Core_ARM,
            "NX2_BRANCH_REGS x20={:#018x} x21={:#018x} x22={:#018x} x23={:#018x} "
            "x24={:#018x} x25={:#018x} x26={:#018x} x27={:#018x} x28={:#018x}",
            ctx.r[20], ctx.r[21], ctx.r[22], ctx.r[23], ctx.r[24],
            ctx.r[25], ctx.r[26], ctx.r[27], ctx.r[28]);

        for (u32 index = 19; index <= 24; ++index) {
            const u64 addr = reg_value(index);
            if (addr >= 0x1000 && m_memory.IsValidVirtualAddressRange(addr, 0x20)) {
                LOG_CRITICAL(
                    Core_ARM,
                    "NX2_BRANCH_OBJECT x{}={:#018x} q0={:#018x} q1={:#018x} "
                    "q2={:#018x} q3={:#018x}",
                    index, addr,
                    m_memory.Read64(addr + 0x00),
                    m_memory.Read64(addr + 0x08),
                    m_memory.Read64(addr + 0x10),
                    m_memory.Read64(addr + 0x18));
            }
        }

        ReturnException(pc, PrefetchAbort);
        return;
    }
"""

if "NX2_BRANCH_FAULT" not in text:
    if old not in text:
        raise SystemExit(f"Could not find NoExecuteFault block in {path}")
    text = text.replace(old, new, 1)

path.write_text(text)
print(f"Installed NX2 null-branch diagnostics: {path}")
