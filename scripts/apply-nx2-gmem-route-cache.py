#!/usr/bin/env python3
"""Pointer loads: find the buffer a pointer points into once, not on every load.

Ocarina of Time on build 189 (Odin 3, Turnip): Kokiri Forest ~13 fps and Hyrule Field ~10 fps with
the GPU 85-90 % busy. The frame traces (builds 164-170) put ~11-30 ms of every frame in scenery
vertex shaders that read memory through pointers (NX2_GMEM: 72 loads, 7 candidate buffers). Since
apply-nx2-gmem-hoist.py the candidates' base addresses are read once per invocation, but every load
still runs the whole search: for each of the 7 buffers a 64-bit subtract, two 64-bit compares, an
AND and two selects (~40 instructions, 64-bit ones split in two on Adreno) to pick the buffer, then
a branch per buffer. A guest load is LDG [Rn.64 + imm], and a vertex shader reads a whole object
through one pointer: the same Rn pair with offsets 0x0, 0x10, 0x20...

So, for programs the hoist already covers (graphics stages, native pointers, no global writes or
atomics), each load whose address is `pointer + non-negative immediate` asks a new route function
for the pointer's buffer once per block; later loads through the same pointer (the same register
pair) in that block reuse the answer and only take the branch to that buffer. The pointer's buffer
is the closest base at or below the pointer, as before; a load at pointer + offset uses the same
buffer (an object does not straddle two bound buffers).

EDEN_NX2_GMEM_ROUTE_CACHE=0 (a switch, full app restart) goes back to the search on every load.
Run after apply-nx2-gmem-hoist.py.
Usage: python3 scripts/apply-nx2-gmem-route-cache.py <upstream-root>
"""
from pathlib import Path
import sys

spirv = Path(sys.argv[1]) / "src/shader_recompiler/backend/spirv"
MARK = "NX2 route cache"
HOIST = "NX2 hoisted global memory bases"


def patch(name, edits, needs_hoist=False):
    path = spirv / name
    text = path.read_text()
    if MARK in text:
        print(f"{name}: already patched")
        return
    if needs_hoist and HOIST not in text:
        raise SystemExit(f"{name}: run apply-nx2-gmem-hoist.py first")
    for old, new in edits:
        if text.count(old) != 1:
            raise SystemExit(f"{name}: anchor found {text.count(old)} times:\n{old}")
        text = text.replace(old, new, 1)
    path.write_text(text)
    print(f"{name}: patched")


patch("spirv_emit_context.h", [
    ("#include <optional>\n", "#include <map>\n#include <optional>\n#include <utility>\n"),
    (
        "    Id nx2_init_global_bases{};\n",
        "    Id nx2_init_global_bases{};\n"
        "    /// " + MARK + ": the route function (pointer -> candidate index) and the loads that\n"
        "    /// take a route, set only where the hoisted bases are; and the routes found in the\n"
        "    /// block being emitted, by pointer (emit_spirv_memory.cpp).\n"
        "    Id nx2_route_global{};\n"
        "    Id nx2_load_routed_u32{};\n"
        "    Id nx2_load_routed_u32x2{};\n"
        "    Id nx2_load_routed_u32x4{};\n"
        "    std::map<std::pair<u64, u64>, Id> nx2_route_cache;\n",
    ),
], needs_hoist=True)

patch("emit_spirv.cpp", [(
    "            nx2_bases_called = true;\n",
    "            nx2_bases_called = true;\n"
    "            // " + MARK + ": a route found in one block is only used in that block.\n"
    "            ctx.nx2_route_cache.clear();\n",
)], needs_hoist=True)

patch("spirv_emit_context.cpp", [(
    "    std::tie(load_global_func_u32x4, write_global_func_u32x4) =\n"
    "        define(&StorageDefinitions::U32x4, storage_types.U32x4, U32[4], sizeof(u32[4]));\n",
    "    std::tie(load_global_func_u32x4, write_global_func_u32x4) =\n"
    "        define(&StorageDefinitions::U32x4, storage_types.U32x4, U32[4], sizeof(u32[4]));\n"
    "    static const bool nx2_route_cache_enabled{[] {\n"
    "        const char* const value{std::getenv(\"EDEN_NX2_GMEM_ROUTE_CACHE\")};\n"
    "        return value == nullptr || value[0] != '0';\n"
    "    }()};\n"
    "    if (nx2_route_cache_enabled && nx2_hoist && !nx2_base_vars.empty()) {\n"
    "        // " + MARK + ": the search define_body runs on every load, as its own function,\n"
    "        // so a pointer's buffer is found once (native pointers: the closest base at or below).\n"
    "        {\n"
    "            const Id func{OpFunction(U32[1], spv::FunctionControlMask::MaskNone,\n"
    "                                     TypeFunction(U32[1], U64))};\n"
    "            const Id pointer{OpFunctionParameter(U64)};\n"
    "            AddLabel();\n"
    "            Id best_distance{Constant(U64, ~u64{0})};\n"
    "            Id route{Const(~0U)};\n"
    "            for (size_t index = 0; index < nx2_base_vars.size(); ++index) {\n"
    "                const Id base{OpLoad(U64, nx2_base_vars[index])};\n"
    "                const Id distance{OpISub(U64, pointer, base)};\n"
    "                const Id better{OpLogicalAnd(U1, OpUGreaterThanEqual(U1, pointer, base),\n"
    "                                             OpULessThan(U1, distance, best_distance))};\n"
    "                best_distance = OpSelect(U64, better, distance, best_distance);\n"
    "                route = OpSelect(U32[1], better, Const(static_cast<u32>(index)), route);\n"
    "            }\n"
    "            OpReturnValue(route);\n"
    "            OpFunctionEnd();\n"
    "            nx2_route_global = func;\n"
    "        }\n"
    "        // The branch per buffer of define_body, taking the route instead of searching.\n"
    "        const auto define_routed{[&](DefPtr ssbo_member, Id element_pointer, Id type, u32 shift) {\n"
    "            const Id func{OpFunction(type, spv::FunctionControlMask::MaskNone,\n"
    "                                     TypeFunction(type, U32[1], U64))};\n"
    "            const Id route{OpFunctionParameter(U32[1])};\n"
    "            const Id addr{OpFunctionParameter(U64)};\n"
    "            Id predecessor{AddLabel()};\n"
    "            Id value{ConstantNull(type)};\n"
    "            for (size_t index = 0; index < nx2_base_vars.size(); ++index) {\n"
    "                const Id routed{OpIEqual(U1, route, Const(static_cast<u32>(index)))};\n"
    "                const Id then_label{OpLabel()};\n"
    "                const Id merge_label{OpLabel()};\n"
    "                OpSelectionMerge(merge_label, spv::SelectionControlMask::MaskNone);\n"
    "                OpBranchConditional(routed, then_label, merge_label);\n"
    "                AddLabel(then_label);\n"
    "                const Id base{OpLoad(U64, nx2_base_vars[index])};\n"
    "                const Id offset{OpUConvert(U32[1], OpISub(U64, addr, base))};\n"
    "                const Id element{OpShiftRightLogical(U32[1], offset, Const(shift))};\n"
    "                const Id result{OpLoad(type, OpAccessChain(element_pointer,\n"
    "                                                           ssbos[index].*ssbo_member, zero,\n"
    "                                                           element))};\n"
    "                OpBranch(merge_label);\n"
    "                AddLabel(merge_label);\n"
    "                value = OpPhi(type, result, then_label, value, predecessor);\n"
    "                predecessor = merge_label;\n"
    "            }\n"
    "            OpReturnValue(value);\n"
    "            OpFunctionEnd();\n"
    "            return func;\n"
    "        }};\n"
    "        // Only the widths define_load reads as one vector (the same conditions).\n"
    "        const auto vector_view{[&](u32 shift) {\n"
    "            return shift <= 2 || (profile.support_descriptor_aliasing &&\n"
    "                                  profile.min_ssbo_alignment >= (1U << shift));\n"
    "        }};\n"
    "        nx2_load_routed_u32 =\n"
    "            define_routed(&StorageDefinitions::U32, storage_types.U32.element, U32[1], 2);\n"
    "        if (vector_view(3)) {\n"
    "            nx2_load_routed_u32x2 = define_routed(&StorageDefinitions::U32x2,\n"
    "                                                  storage_types.U32x2.element, U32[2], 3);\n"
    "        }\n"
    "        if (vector_view(4)) {\n"
    "            nx2_load_routed_u32x4 = define_routed(&StorageDefinitions::U32x4,\n"
    "                                                  storage_types.U32x4.element, U32[4], 4);\n"
    "        }\n"
    "    }\n",
)], needs_hoist=True)

patch("emit_spirv_instructions.h", [
    ("Id EmitLoadGlobal32(EmitContext& ctx, Id address);\n",
     "// " + MARK + ": the loads look at their address's IR to share a pointer's route.\n"
     "Id EmitLoadGlobal32(EmitContext& ctx, IR::Inst* inst, Id address);\n"),
    ("Id EmitLoadGlobal64(EmitContext& ctx, Id address);\n",
     "Id EmitLoadGlobal64(EmitContext& ctx, IR::Inst* inst, Id address);\n"),
    ("Id EmitLoadGlobal128(EmitContext& ctx, Id address);\n",
     "Id EmitLoadGlobal128(EmitContext& ctx, IR::Inst* inst, Id address);\n"),
])

patch("emit_spirv_memory.cpp", [
    (
        "Id EmitLoadGlobal32(EmitContext& ctx, Id address) {\n"
        "    if (ctx.profile.support_int64) {\n"
        "        return ctx.OpFunctionCall(ctx.U32[1], ctx.load_global_func_u32, address);\n",
        "namespace {\n"
        "// " + MARK + ": an IR value as a key (instructions by address, immediates tagged).\n"
        "u64 Nx2ValueKey(const IR::Value& value) {\n"
        "    const IR::Value resolved{value.Resolve()};\n"
        "    if (resolved.IsImmediate()) {\n"
        "        return (u64{resolved.U32()} << 1) | 1;\n"
        "    }\n"
        "    return static_cast<u64>(reinterpret_cast<uintptr_t>(resolved.InstRecursive()));\n"
        "}\n"
        "\n"
        "// A load at `pointer + immediate` through the route found for the pointer in this block.\n"
        "// Returns no Id when the load does not have that shape (or the program has no routes).\n"
        "Id Nx2RoutedLoad(EmitContext& ctx, IR::Inst* inst, Id address, Id func, Id type) {\n"
        "    if (!Sirit::ValidId(ctx.nx2_route_global) || !Sirit::ValidId(func)) {\n"
        "        return Id{};\n"
        "    }\n"
        "    const IR::Value addr{inst->Arg(0).Resolve()};\n"
        "    if (addr.IsImmediate()) {\n"
        "        return Id{};\n"
        "    }\n"
        "    IR::Value pointer{addr};\n"
        "    const IR::Inst* const add{addr.InstRecursive()};\n"
        "    if (add->GetOpcode() == IR::Opcode::IAdd64) {\n"
        "        const IR::Value offset{add->Arg(1).Resolve()};\n"
        "        const IR::Value base{add->Arg(0).Resolve()};\n"
        "        if (!offset.IsImmediate() || base.IsImmediate()) {\n"
        "            return Id{};\n"
        "        }\n"
        "        // Only forward offsets: the pointer's buffer then also holds the address.\n"
        "        if (offset.U64() >= 0x1000000) {\n"
        "            return Id{};\n"
        "        }\n"
        "        pointer = base;\n"
        "    }\n"
        "    const IR::Inst* const pointer_inst{pointer.InstRecursive()};\n"
        "    // Every LDG packs its register pair again: key on the pair, not the pack.\n"
        "    std::pair<u64, u64> key{static_cast<u64>(reinterpret_cast<uintptr_t>(pointer_inst)), 0};\n"
        "    if (pointer_inst->GetOpcode() == IR::Opcode::PackUint2x32) {\n"
        "        const IR::Value pair{pointer_inst->Arg(0).Resolve()};\n"
        "        if (!pair.IsImmediate() &&\n"
        "            pair.InstRecursive()->GetOpcode() == IR::Opcode::CompositeConstructU32x2) {\n"
        "            const IR::Inst* const words{pair.InstRecursive()};\n"
        "            key = {Nx2ValueKey(words->Arg(0)), Nx2ValueKey(words->Arg(1)) | (u64{1} << 63)};\n"
        "        }\n"
        "    }\n"
        "    Id route{};\n"
        "    if (const auto it{ctx.nx2_route_cache.find(key)}; it != ctx.nx2_route_cache.end()) {\n"
        "        route = it->second;\n"
        "    } else {\n"
        "        route = ctx.OpFunctionCall(ctx.U32[1], ctx.nx2_route_global, ctx.Def(pointer));\n"
        "        ctx.nx2_route_cache.emplace(key, route);\n"
        "    }\n"
        "    return ctx.OpFunctionCall(type, func, route, address);\n"
        "}\n"
        "} // Anonymous namespace\n"
        "\n"
        "Id EmitLoadGlobal32(EmitContext& ctx, IR::Inst* inst, Id address) {\n"
        "    if (ctx.profile.support_int64) {\n"
        "        if (const Id routed{Nx2RoutedLoad(ctx, inst, address, ctx.nx2_load_routed_u32,\n"
        "                                          ctx.U32[1])};\n"
        "            Sirit::ValidId(routed)) {\n"
        "            return routed;\n"
        "        }\n"
        "        return ctx.OpFunctionCall(ctx.U32[1], ctx.load_global_func_u32, address);\n",
    ),
    (
        "Id EmitLoadGlobal64(EmitContext& ctx, Id address) {\n"
        "    if (ctx.profile.support_int64) {\n",
        "Id EmitLoadGlobal64(EmitContext& ctx, IR::Inst* inst, Id address) {\n"
        "    if (ctx.profile.support_int64) {\n"
        "        if (const Id routed{Nx2RoutedLoad(ctx, inst, address, ctx.nx2_load_routed_u32x2,\n"
        "                                          ctx.U32[2])};\n"
        "            Sirit::ValidId(routed)) {\n"
        "            return routed;\n"
        "        }\n",
    ),
    (
        "Id EmitLoadGlobal128(EmitContext& ctx, Id address) {\n"
        "    if (ctx.profile.support_int64) {\n",
        "Id EmitLoadGlobal128(EmitContext& ctx, IR::Inst* inst, Id address) {\n"
        "    if (ctx.profile.support_int64) {\n"
        "        if (const Id routed{Nx2RoutedLoad(ctx, inst, address, ctx.nx2_load_routed_u32x4,\n"
        "                                          ctx.U32[4])};\n"
        "            Sirit::ValidId(routed)) {\n"
        "            return routed;\n"
        "        }\n",
    ),
])
print("pointer route cache installed")
