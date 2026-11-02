#!/usr/bin/env python3
from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: apply-nx2-null-call-bypass.py <upstream-dir>")

root = Path(sys.argv[1])
path = root / "src" / "core" / "arm" / "dynarmic" / "arm_dynarmic_64.cpp"
text = path.read_text()

anchor = """        ReturnException(pc, PrefetchAbort);
        return;
    }
"""

block = """        // Experimental DKB probe: if this specific title calls through a near-null
        // target, treat at most eight such calls as no-op callbacks. This is not a
        // compatibility fix; it exists to expose the next real dependency and is
        // deliberately title-scoped and count-limited.
        if (pc < 0x100 && m_process != nullptr &&
            m_process->GetProgramId() == 0x040074A01BF12000ULL && ctx.lr >= 0x1000) {
            static std::atomic<u32> nx2_near_null_bypass_count{0};
            const u32 bypass_index = nx2_near_null_bypass_count.fetch_add(1) + 1;
            if (bypass_index <= 8) {
                LOG_CRITICAL(
                    Core_ARM,
                    "NX2_NEAR_NULL_BYPASS index={} target={:#018x} resume_lr={:#018x} x0={:#018x}",
                    bypass_index, pc, ctx.lr, ctx.r[0]);
                ctx.pc = ctx.lr;
                m_parent.SetContext(ctx);
                m_parent.m_jit->HaltExecution(BreakLoop);
                return;
            }
            LOG_CRITICAL(
                Core_ARM,
                "NX2_NEAR_NULL_BYPASS_LIMIT target={:#018x} lr={:#018x}; falling through",
                pc, ctx.lr);
        }

        ReturnException(pc, PrefetchAbort);
        return;
    }
"""

if "NX2_NEAR_NULL_BYPASS" not in text:
    if "NX2_BRANCH_FAULT" not in text:
        raise SystemExit("diagnostic patch must be applied before bypass patch")
    if anchor not in text:
        raise SystemExit(f"Could not find NoExecuteFault tail in {path}")
    text = text.replace(anchor, block, 1)

path.write_text(text)
print(f"Installed title-scoped NX2 near-null call bypass probe: {path}")
