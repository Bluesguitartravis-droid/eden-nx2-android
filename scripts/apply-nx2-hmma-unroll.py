#!/usr/bin/env python3
"""Software HMMA: the K loop (4 or 8 steps, a constant trip count) is marked DontUnroll, with a
counter-dependent branch for the K=8 half rounding. On Adreno every step then pays the loop
control and the branch, and the compiler cannot overlap one step's workgroup-memory loads with
the previous step's FMAs. Ask for unrolling instead: the rounding branch then folds to the two
fixed steps that need it. EDEN_NX2_HMMA_UNROLL=0 keeps DontUnroll."""
from pathlib import Path
import sys

f = Path(sys.argv[1]).resolve() / "src/shader_recompiler/backend/spirv/emit_spirv_tensor.cpp"
t = f.read_text()
old = "        ctx.OpLoopMerge(end, next, spv::LoopControlMask::DontUnroll);\n"
new = """        static const bool nx2_unroll = [] {
            const char* v = std::getenv("EDEN_NX2_HMMA_UNROLL");
            return !(v && v[0] == '0');
        }();
        ctx.OpLoopMerge(end, next, nx2_unroll ? spv::LoopControlMask::Unroll
                                              : spv::LoopControlMask::DontUnroll);
"""
if "EDEN_NX2_HMMA_UNROLL" not in t:
    if old not in t:
        raise SystemExit("HMMA loop merge not found")
    t = t.replace(old, new, 1)
    old_flat = "        ctx.OpSelectionMerge(round_merge, spv::SelectionControlMask::DontFlatten);\n"
    if old_flat in t:
        t = t.replace(old_flat, "        ctx.OpSelectionMerge(round_merge, nx2_unroll ? spv::SelectionControlMask::MaskNone\n"
                                "                                                     : spv::SelectionControlMask::DontFlatten);\n", 1)
    if "#include <cstdlib>" not in t:
        t = "#include <cstdlib>\n" + t
    f.write_text(t)
print("HMMA K loop unrolled")
