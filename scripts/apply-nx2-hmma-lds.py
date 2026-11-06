#!/usr/bin/env python3
"""Software HMMA through workgroup memory instead of subgroup shuffles (compute).

Lichii emulates Ampere matrix multiplies (DKB's upscaler network) with
OpGroupNonUniformShuffle at per-lane indices. Turnip (ir3) has no such shuffle:
each becomes a loop of rotates, one pass per distinct lane offset. With tensor
emulation on, the Adreno is 85-90% busy in DKB gameplay at any resolution; off,
11-19% and a steady 30 fps (but a purple screen). BananaNative measured the
same network at 29 ms on Turnip with shuffles and 9 ms through workgroup memory.

Here each lane stores its A and B words once per call, one subgroup barrier,
and every gather becomes a workgroup load; a barrier at the end protects the
next call. The arithmetic is unchanged, so results are bit-identical.
EDEN_NX2_HMMA_SHUFFLE=1 restores the shuffle version.
"""
from pathlib import Path
import sys

f = Path(sys.argv[1]) / "src/shader_recompiler/backend/spirv/emit_spirv_tensor.cpp"
t = f.read_text()
if "hmma_lds" in t:
    print("HMMA LDS already applied"); sys.exit(0)

def rep(old, new):
    global t
    if old not in t:
        raise SystemExit(f"emit_spirv_tensor.cpp shape changed near: {old[:70]!r}")
    t = t.replace(old, new, 1)

rep('#include "common/common_types.h"\n', '#include "common/common_types.h"\n#include <cstdlib>\n')

rep('''    for (u32 variant = 0; variant < 2; ++variant) {
        if (!needed[variant]) continue;''', '''    // Eden NX2 Android (hmma_lds): gathers through workgroup memory in compute shaders.
    const u32 invocations =
        program.workgroup_size[0] * program.workgroup_size[1] * program.workgroup_size[2];
    const u32 lds_words = (invocations + 128) * 6;
    static const bool force_shuffle = std::getenv("EDEN_NX2_HMMA_SHUFFLE") != nullptr;
    const bool use_lds = (needed[0] || needed[1]) && !force_shuffle &&
                         program.stage == Stage::Compute && invocations > 0 &&
                         // Explicit-layout workgroup blocks alias each other: ours would overlap.
                         !ctx.uses_explicit_workgroup_layout &&
                         program.shared_memory_size + lds_words * 4 <= 32768;
    Id lds_var{}, lds_elem_ptr{}, lds_subgroup_id{}, lds_subgroup_size{};
    if (use_lds) {
        ctx.AddCapability(spv::Capability::GroupNonUniform);
        const Id array = ctx.TypeArray(ctx.U32[1], ctx.Const(lds_words));
        lds_var = ctx.AddGlobalVariable(ctx.TypePointer(spv::StorageClass::Workgroup, array),
                                        spv::StorageClass::Workgroup);
        ctx.interfaces.push_back(lds_var);
        lds_elem_ptr = ctx.TypePointer(spv::StorageClass::Workgroup, ctx.U32[1]);
        const Id input_ptr = ctx.TypePointer(spv::StorageClass::Input, ctx.U32[1]);
        lds_subgroup_id = ctx.AddGlobalVariable(input_ptr, spv::StorageClass::Input);
        ctx.Decorate(lds_subgroup_id, spv::Decoration::BuiltIn, spv::BuiltIn::SubgroupId);
        ctx.interfaces.push_back(lds_subgroup_id);
        lds_subgroup_size = ctx.AddGlobalVariable(input_ptr, spv::StorageClass::Input);
        ctx.Decorate(lds_subgroup_size, spv::Decoration::BuiltIn, spv::BuiltIn::SubgroupSize);
        ctx.interfaces.push_back(lds_subgroup_size);
    }
    for (u32 variant = 0; variant < 2; ++variant) {
        if (!needed[variant]) continue;''')

rep('''        const Id lane = ctx.OpLoad(u32ty, ctx.subgroup_local_invocation_id);
''', '''        const Id lane = ctx.OpLoad(u32ty, ctx.subgroup_local_invocation_id);
        // Subgroup-scope barrier covering workgroup memory (AcquireRelease | WorkgroupMemory).
        const auto lds_barrier = [&] { ctx.OpControlBarrier(imm(3), imm(3), imm(0x108)); };
        Id group_base{};
        if (use_lds) {
            group_base = ctx.OpIMul(u32ty, ctx.OpLoad(u32ty, lds_subgroup_id),
                                    ctx.OpLoad(u32ty, lds_subgroup_size));
            const Id own = ctx.OpIMul(u32ty, add(group_base, lane), imm(6));
            for (u32 i = 0; i < 6; ++i) {
                ctx.OpStore(ctx.OpAccessChain(lds_elem_ptr, lds_var, add(own, imm(i))), inputs[i]);
            }
            lds_barrier();
        }
        // The word `word` (0-3 A, 4-5 B) of lane `src_lane` of this subgroup.
        const auto fetch = [&](Id value, Id src_lane, Id word) -> Id {
            if (!use_lds) {
                return ctx.OpGroupNonUniformShuffle(u32ty, imm(3), value, src_lane);
            }
            const Id index = add(ctx.OpIMul(u32ty, add(group_base, src_lane), imm(6)), word);
            return ctx.OpLoad(u32ty, ctx.OpAccessChain(lds_elem_ptr, lds_var, index));
        };
''')

rep('''            bs[col] = unpack(ctx.OpGroupNonUniformShuffle(u32ty, imm(3), b,
                                                          add(b_lane, imm(col * 4))));''',
    '''            bs[col] = unpack(fetch(b, add(b_lane, imm(col * 4)),
                                   ctx.OpSelect(u32ty, upper, imm(5), imm(4))));''')

rep('''            const Id as = unpack(ctx.OpGroupNonUniformShuffle(u32ty, imm(3), a, a_lane));''',
    '''            const Id as = unpack(fetch(a, a_lane,
                                       ctx.OpSelect(u32ty, upper, imm(row + 2), imm(row))));''')

rep('''        ctx.AddLabel(end);
        std::array<Id, 2> packed;''', '''        ctx.AddLabel(end);
        if (use_lds) {
            lds_barrier();
        }
        std::array<Id, 2> packed;''')

f.write_text(t)
print("Software HMMA gathers through workgroup memory (hmma_lds)")
