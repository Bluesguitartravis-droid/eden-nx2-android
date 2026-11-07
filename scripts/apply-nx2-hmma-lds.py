#!/usr/bin/env python3
"""Software HMMA and the upscaler's per-lane shuffles through workgroup memory (Turnip).

Lichii emulates Ampere matrix multiplies (DKB's upscaler network) with
OpGroupNonUniformShuffle at per-lane indices, and lowers the network's LDSM/MOVM
gathers to such shuffles too. Turnip (ir3) has no per-lane-index shuffle: each
becomes a loop of rotates. With tensor emulation on, the Adreno is 85-90% busy in
DKB gameplay at any resolution; off, 11-19% and 30 fps (purple screen).
BananaNative measured this network at 29 ms on Turnip with shuffles, 9 ms through
workgroup memory.

In compute programs that use HMMA:
- mode 2 (room for 7 words per lane): each HMMA call stores the lane's A/B words
  once, one subgroup barrier, gathers are workgroup loads, a barrier at the end;
  the program's other per-lane shuffles exchange through one row of workgroup
  memory (store, barrier, load, barrier).
- mode 1 (room for 1 word per lane): every gather, HMMA ones too, uses the row.
Works with and without explicit workgroup layout (our block sits after the
program's own shared memory). Each program logs its choice (NX2 HMMA lowering).
EDEN_NX2_HMMA_SHUFFLE=1 keeps the shuffles. The arithmetic is unchanged.
"""
from pathlib import Path
import sys

root = Path(sys.argv[1])
spirv = root / "src/shader_recompiler/backend/spirv"
f = spirv / "emit_spirv_tensor.cpp"
t = f.read_text()
if "nx2_lds_var" in t:
    print("HMMA LDS already applied"); sys.exit(0)

def rep(text, old, new, where):
    if old not in text:
        raise SystemExit(f"{where} shape changed near: {old[:70]!r}")
    return text.replace(old, new, 1)

t = rep(t, '#include "common/common_types.h"\n',
        '#include "common/common_types.h"\n#include "common/logging.h"\n#include <cstdlib>\n', "tensor")

t = rep(t, '''    for (u32 variant = 0; variant < 2; ++variant) {
        if (!needed[variant]) continue;''', '''    // Eden NX2 Android (hmma_lds): gathers through workgroup memory in compute shaders.
    const u32 invocations =
        program.workgroup_size[0] * program.workgroup_size[1] * program.workgroup_size[2];
    const u32 slots = invocations + 128; // SubgroupId * SubgroupSize + lane stays below this
    const u32 base_bytes = (program.shared_memory_size + 15U) & ~15U;
    static const bool force_shuffle = std::getenv("EDEN_NX2_HMMA_SHUFFLE") != nullptr;
    const bool candidate = (needed[0] || needed[1]) && !force_shuffle &&
                           program.stage == Stage::Compute && invocations > 0 &&
                           ctx.profile.SupportsSubgroupStage(program.stage);
    u32 lds_mode = 0;
    if (candidate && base_bytes + slots * 7 * 4 <= 32768) {
        lds_mode = 2;
    } else if (candidate && base_bytes + slots * 4 <= 32768) {
        lds_mode = 1;
    }
    if (needed[0] || needed[1]) {
        LOG_INFO(Shader_SPIRV,
                 "NX2 HMMA lowering: invocations={} shared={} explicit_layout={} mode={}",
                 invocations, program.shared_memory_size, ctx.uses_explicit_workgroup_layout,
                 lds_mode);
    }
    if (lds_mode != 0) {
        ctx.AddCapability(spv::Capability::GroupNonUniform);
        const u32 hmma_words = lds_mode == 2 ? slots * 6 : 0;
        const Id array = ctx.TypeArray(ctx.U32[1], ctx.Const(hmma_words + slots));
        ctx.nx2_lds_elem_ptr = ctx.TypePointer(spv::StorageClass::Workgroup, ctx.U32[1]);
        if (ctx.uses_explicit_workgroup_layout) {
            // Explicit-layout blocks alias from offset 0: start ours after the program's memory.
            ctx.Decorate(array, spv::Decoration::ArrayStride, 4U);
            const Id block = ctx.TypeStruct(array);
            ctx.MemberDecorate(block, 0U, spv::Decoration::Offset, base_bytes);
            ctx.Decorate(block, spv::Decoration::Block);
            ctx.nx2_lds_var = ctx.AddGlobalVariable(
                ctx.TypePointer(spv::StorageClass::Workgroup, block), spv::StorageClass::Workgroup);
            ctx.Decorate(ctx.nx2_lds_var, spv::Decoration::Aliased);
            ctx.nx2_lds_explicit = true;
        } else {
            ctx.nx2_lds_var = ctx.AddGlobalVariable(
                ctx.TypePointer(spv::StorageClass::Workgroup, array), spv::StorageClass::Workgroup);
        }
        ctx.interfaces.push_back(ctx.nx2_lds_var);
        const Id input_ptr = ctx.TypePointer(spv::StorageClass::Input, ctx.U32[1]);
        ctx.nx2_subgroup_id = ctx.AddGlobalVariable(input_ptr, spv::StorageClass::Input);
        ctx.Decorate(ctx.nx2_subgroup_id, spv::Decoration::BuiltIn, spv::BuiltIn::SubgroupId);
        ctx.interfaces.push_back(ctx.nx2_subgroup_id);
        ctx.nx2_subgroup_size = ctx.AddGlobalVariable(input_ptr, spv::StorageClass::Input);
        ctx.Decorate(ctx.nx2_subgroup_size, spv::Decoration::BuiltIn, spv::BuiltIn::SubgroupSize);
        ctx.interfaces.push_back(ctx.nx2_subgroup_size);
        ctx.nx2_rows_base = hmma_words;
        ctx.nx2_lds_mode = lds_mode;
    }
    for (u32 variant = 0; variant < 2; ++variant) {
        if (!needed[variant]) continue;''', "tensor")

t = rep(t, '''        const Id lane = ctx.OpLoad(u32ty, ctx.subgroup_local_invocation_id);
''', '''        const Id lane = ctx.OpLoad(u32ty, ctx.subgroup_local_invocation_id);
        // Subgroup-scope barrier covering workgroup memory (AcquireRelease | WorkgroupMemory).
        const auto lds_barrier = [&] { ctx.OpControlBarrier(imm(3), imm(3), imm(0x108)); };
        const auto lds_at = [&](Id index) {
            return ctx.nx2_lds_explicit
                       ? ctx.OpAccessChain(ctx.nx2_lds_elem_ptr, ctx.nx2_lds_var, imm(0), index)
                       : ctx.OpAccessChain(ctx.nx2_lds_elem_ptr, ctx.nx2_lds_var, index);
        };
        Id group_base{};
        if (lds_mode != 0) {
            group_base = ctx.OpIMul(u32ty, ctx.OpLoad(u32ty, ctx.nx2_subgroup_id),
                                    ctx.OpLoad(u32ty, ctx.nx2_subgroup_size));
        }
        if (lds_mode == 2) {
            const Id own = ctx.OpIMul(u32ty, add(group_base, lane), imm(6));
            for (u32 i = 0; i < 6; ++i) {
                ctx.OpStore(lds_at(add(own, imm(i))), inputs[i]);
            }
            lds_barrier();
        }
        // `value` of lane `src_lane`; in mode 2 it is word `word` (0-3 A, 4-5 B) of the staging.
        const auto fetch = [&](Id value, Id src_lane, Id word) -> Id {
            if (lds_mode == 0) {
                return ctx.OpGroupNonUniformShuffle(u32ty, imm(3), value, src_lane);
            }
            if (lds_mode == 2) {
                return ctx.OpLoad(u32ty,
                    lds_at(add(ctx.OpIMul(u32ty, add(group_base, src_lane), imm(6)), word)));
            }
            const Id row = add(imm(ctx.nx2_rows_base), group_base);
            ctx.OpStore(lds_at(add(row, lane)), value);
            lds_barrier();
            const Id result = ctx.OpLoad(u32ty, lds_at(add(row, src_lane)));
            lds_barrier();
            return result;
        };
''', "tensor")

t = rep(t, '''            bs[col] = unpack(ctx.OpGroupNonUniformShuffle(u32ty, imm(3), b,
                                                          add(b_lane, imm(col * 4))));''',
    '''            bs[col] = unpack(fetch(b, add(b_lane, imm(col * 4)),
                                   ctx.OpSelect(u32ty, upper, imm(5), imm(4))));''', "tensor")
t = rep(t, '''            const Id as = unpack(ctx.OpGroupNonUniformShuffle(u32ty, imm(3), a, a_lane));''',
    '''            const Id as = unpack(fetch(a, a_lane,
                                       ctx.OpSelect(u32ty, upper, imm(row + 2), imm(row))));''', "tensor")
t = rep(t, '''        ctx.AddLabel(end);
        std::array<Id, 2> packed;''', '''        ctx.AddLabel(end);
        if (lds_mode == 2) {
            lds_barrier();
        }
        std::array<Id, 2> packed;''', "tensor")
f.write_text(t)

h = spirv / "spirv_emit_context.h"
ht = h.read_text()
ht = rep(ht, "    bool uses_explicit_workgroup_layout{};\n", """    bool uses_explicit_workgroup_layout{};
    // Eden NX2 Android: workgroup-memory gathers for HMMA programs (emit_spirv_tensor.cpp).
    Id nx2_lds_var{};
    Id nx2_lds_elem_ptr{};
    Id nx2_subgroup_id{};
    Id nx2_subgroup_size{};
    u32 nx2_rows_base{};
    u32 nx2_lds_mode{};
    bool nx2_lds_explicit{};
""", "spirv_emit_context.h")
h.write_text(ht)

w = spirv / "emit_spirv_warp.cpp"
wt = w.read_text()
wt = rep(wt, """Id ShuffleAbsolute(EmitContext& ctx, Id value, Id src_thread_id) {
""", """Id ShuffleAbsolute(EmitContext& ctx, Id value, Id src_thread_id) {
    if (ctx.nx2_lds_mode != 0) {
        // Eden NX2 Android: Turnip lowers a per-lane-index shuffle to a loop of rotates. Store
        // this lane's word, subgroup barrier, load the source lane's, barrier (so a later
        // exchange, also in a loop, cannot overwrite a word before every lane has read it).
        const auto at = [&](Id index) {
            return ctx.nx2_lds_explicit
                       ? ctx.OpAccessChain(ctx.nx2_lds_elem_ptr, ctx.nx2_lds_var,
                                           ctx.u32_zero_value, index)
                       : ctx.OpAccessChain(ctx.nx2_lds_elem_ptr, ctx.nx2_lds_var, index);
        };
        const Id barrier_scope{ctx.Const(3U)};
        const Id semantics{ctx.Const(0x108U)};
        const Id row{ctx.OpIAdd(ctx.U32[1], ctx.Const(ctx.nx2_rows_base),
            ctx.OpIMul(ctx.U32[1], ctx.OpLoad(ctx.U32[1], ctx.nx2_subgroup_id),
                       ctx.OpLoad(ctx.U32[1], ctx.nx2_subgroup_size)))};
        ctx.OpStore(at(ctx.OpIAdd(ctx.U32[1], row, GetThreadId(ctx))), value);
        ctx.OpControlBarrier(barrier_scope, barrier_scope, semantics);
        const Id result{ctx.OpLoad(ctx.U32[1], at(ctx.OpIAdd(ctx.U32[1], row, src_thread_id)))};
        ctx.OpControlBarrier(barrier_scope, barrier_scope, semantics);
        return result;
    }
""", "emit_spirv_warp.cpp")
w.write_text(wt)
print("HMMA and upscaler shuffles through workgroup memory (hmma_lds v3)")
