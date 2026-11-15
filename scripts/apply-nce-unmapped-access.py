#!/usr/bin/env python3
"""NCE: a guest load or store to unmapped memory (Ocarina reads near address 0 hundreds of
times at boot) skipped the instruction and left the destination register holding whatever it
held before. Ocarina then sees garbage and panics (svcBreak) where Dynarmic, which reads
unmapped memory as zero and drops the store, carries on. Run the faulting instruction
through NCE's own interpreter (the alignment-fault path) first: its memory accesses go through
the emulated memory, so unmapped loads read zero as on Dynarmic. Instructions the
interpreter does not know still fall back to skipping."""
from pathlib import Path
import sys

f = Path(sys.argv[1]).resolve() / "src/core/arm/nce/arm_nce.cpp"
t = f.read_text()
old = """    if (memory.InvalidateNCE(addr, Memory::LICHII_PAGESIZE)) {
        // We handled the access successfully and are returning to guest code.
        return true;
    }

    // We couldn't handle the access.
    return HandleFailedGuestFault(guest_ctx, raw_info, raw_context);
}"""
new = """    if (memory.InvalidateNCE(addr, Memory::LICHII_PAGESIZE)) {
        // We handled the access successfully and are returning to guest code.
        return true;
    }

    // An access to unmapped memory: execute it on the emulated memory, as Dynarmic does
    // (loads read zero, stores are dropped), rather than skipping it with a stale register.
    {
        auto& host_ctx = static_cast<ucontext_t*>(raw_context)->uc_mcontext;
        if (host_ctx.pc != reinterpret_cast<u64>(info->si_addr)) {
            auto* fpctx = GetFloatingPointState(host_ctx);
            if (const auto next_pc = MatchAndExecuteOneInstruction(memory, &host_ctx, fpctx)) {
                host_ctx.pc = *next_pc;
                return true;
            }
        }
    }

    // We couldn't handle the access.
    return HandleFailedGuestFault(guest_ctx, raw_info, raw_context);
}"""
if "loads read zero, stores are dropped" not in t:
    if old not in t:
        raise SystemExit("HandleGuestAccessFault shape changed")
    t = t.replace(old, new, 1)
    f.write_text(t)
print("NCE unmapped accesses run through the interpreter")
