#!/usr/bin/env python3
"""Pointer loads: work out the candidate buffers' base addresses once per invocation.

Build 164's frame trace of Ocarina of Time (Turnip, Kokiri Forest, GPU 60-90 ms a frame):
vertex shaders that load through pointers (NX2_GMEM: 72 loads, 7 candidate buffers, 5 of them
found through a pointer read from another buffer) take about 11 ms of every frame, one draw up to
11 ms on its own. Every pointer load called a helper that recomputed each candidate's base: a
constant buffer read per candidate plus two storage buffer reads for each indirect one, so about
17 memory reads before the load itself, 72 times a vertex. Banana Native measured the same shape
at 146-171 ms on Turnip against 2.5 ms on Qualcomm's driver.

For native programs (native_global_bounds) that write no storage buffer, the bases are now read
once, by a function the entry point calls first, into private variables that every pointer load
reads. Constant buffers cannot change during a draw, and the pointer holders are storage buffers
the program does not write.

Build 165 computed them lazily (a private bool and a branch in every helper) and build 166 in an
entry-point function: Turnip's compiler crashed on both, at the same place, building Ocarina's
compute programs. From build 167 only graphics stages are changed (where the measured cost is).
EDEN_NX2_GMEM_HOIST=0 restores the old code.
Usage: python3 scripts/apply-nx2-gmem-hoist.py <upstream-root>
"""
from pathlib import Path
import sys

spirv = Path(sys.argv[1]) / "src/shader_recompiler/backend/spirv"
MARK = "NX2 hoisted global memory bases"


def patch(name, edits):
    path = spirv / name
    text = path.read_text()
    if MARK in text:
        print(f"{name}: already patched")
        return
    for old, new in edits:
        if text.count(old) != 1:
            raise SystemExit(f"{name}: anchor found {text.count(old)} times:\n{old}")
        text = text.replace(old, new, 1)
    if name.endswith(".cpp") and "#include <cstdlib>\n" not in text:
        text = text.replace("#include <algorithm>\n", "#include <algorithm>\n#include <cstdlib>\n", 1)
    path.write_text(text)
    print(f"{name}: patched")


patch("spirv_emit_context.h", [(
    "    Id load_global_func_u32{};\n",
    "    Id load_global_func_u32{};\n"
    "    /// " + MARK + ": called first by the entry point when set.\n"
    "    Id nx2_init_global_bases{};\n",
)])

patch("emit_spirv.cpp", [(
    "            current_block = node.data.block;\n"
    "            ctx.AddLabel(label);\n",
    "            const bool nx2_entry_block{current_block == nullptr};\n"
    "            current_block = node.data.block;\n"
    "            ctx.AddLabel(label);\n"
    "            if (nx2_entry_block && Sirit::ValidId(ctx.nx2_init_global_bases)) {\n"
    "                // " + MARK + "\n"
    "                ctx.OpFunctionCall(ctx.void_id, ctx.nx2_init_global_bases);\n"
    "            }\n",
)])

patch("spirv_emit_context.cpp", [
    (
        "    using DefPtr = Id StorageDefinitions::*;\n"
        "    const Id zero{u32_zero_value};\n",
        "    using DefPtr = Id StorageDefinitions::*;\n"
        "    const Id zero{u32_zero_value};\n"
        "    // " + MARK + ": private variables filled once by a function the entry point\n"
        "    // calls first (no branch, no bool: build 165's lazy version crashed Turnip's compiler).\n"
        "    static const bool nx2_hoist_enabled{[] {\n"
        "        const char* const value{std::getenv(\"EDEN_NX2_GMEM_HOIST\")};\n"
        "        return value == nullptr || value[0] != '0';\n"
        "    }()};\n"
        "    // Graphics stages only: Turnip's compiler crashed on Ocarina's compute programs with the\n"
        "    // bases hoisted (builds 165 and 166, same place in libvulkan_freedreno).\n"
        "    const bool nx2_hoist{nx2_hoist_enabled && stage != Stage::Compute &&\n"
        "                         stage != Stage::Task && stage != Stage::Mesh &&\n"
        "                         info.native_global_bounds &&\n"
        "                         !info.storage_buffers_descriptors.empty() &&\n"
        "                         std::ranges::none_of(info.storage_buffers_descriptors,\n"
        "                                              [](const auto& desc) { return desc.is_written; })};\n"
        "    std::vector<Id> nx2_base_vars;\n",
    ),
    (
        "        if (info.native_global_bounds) {\n"
        "            // Native pointers carry no NVN size word. The address belongs to the candidate buffer\n"
        "            // whose base is the closest one at or below it (bounds come from the binding).\n"
        "            std::vector<Id> bases(num_buffers);\n"
        "            Id best_index{Const(~0U)};\n"
        "            Id best_distance{Constant(U64, ~u64{0})};\n"
        "            for (size_t index = 0; index < num_buffers; ++index) {\n"
        "                bases[index] = base_address(index);\n",
        "        if (nx2_hoist && nx2_base_vars.empty()) {\n"
        "            // " + MARK + ": the first helper defines the variables and the function\n"
        "            // that fills them. The helper's own block is suspended while it is emitted.\n"
        "            const Id u64_private{TypePointer(spv::StorageClass::Private, U64)};\n"
        "            for (size_t index = 0; index < num_buffers; ++index) {\n"
        "                nx2_base_vars.push_back(AddGlobalVariable(u64_private, spv::StorageClass::Private));\n"
        "            }\n"
        "            if (profile.supported_spirv >= 0x00010400) {\n"
        "                interfaces.insert(interfaces.end(), nx2_base_vars.begin(), nx2_base_vars.end());\n"
        "            }\n"
        "        }\n"
        "        if (info.native_global_bounds) {\n"
        "            // Native pointers carry no NVN size word. The address belongs to the candidate buffer\n"
        "            // whose base is the closest one at or below it (bounds come from the binding).\n"
        "            std::vector<Id> bases(num_buffers);\n"
        "            Id best_index{Const(~0U)};\n"
        "            Id best_distance{Constant(U64, ~u64{0})};\n"
        "            for (size_t index = 0; index < num_buffers; ++index) {\n"
        "                bases[index] = nx2_hoist ? OpLoad(U64, nx2_base_vars[index])\n"
        "                                         : base_address(index);\n",
    ),
    (
        "    std::tie(load_global_func_u32, write_global_func_u32) =\n"
        "        define(&StorageDefinitions::U32, storage_types.U32, U32[1], sizeof(u32));\n",
        "    std::tie(load_global_func_u32, write_global_func_u32) =\n"
        "        define(&StorageDefinitions::U32, storage_types.U32, U32[1], sizeof(u32));\n"
        "    if (nx2_hoist && !nx2_base_vars.empty()) {\n"
        "        // " + MARK + ": the function the entry point calls first, the same base\n"
        "        // computation as define_body's base_address.\n"
        "        const Id init_func{OpFunction(void_id, spv::FunctionControlMask::MaskNone,\n"
        "                                      TypeFunction(void_id))};\n"
        "        AddLabel();\n"
        "        const u64 ssbo_align_mask{~(profile.min_ssbo_alignment - 1U)};\n"
        "        for (size_t index = 0; index < info.storage_buffers_descriptors.size(); ++index) {\n"
        "            const auto& ssbo{info.storage_buffers_descriptors[index]};\n"
        "            const Id words{EmitGetCbufU32x2(*this, IR::Value{ssbo.cbuf_index},\n"
        "                                           IR::Value{ssbo.cbuf_offset})};\n"
        "            Id base{};\n"
        "            if (ssbo.indirect_offset == ~0U) {\n"
        "                base = OpBitwiseAnd(U64, OpBitcast(U64, words), Constant(U64, ssbo_align_mask));\n"
        "            } else {\n"
        "                const auto holder{std::ranges::find_if(\n"
        "                    info.storage_buffers_descriptors, [&ssbo](const auto& desc) {\n"
        "                        return desc.cbuf_index == ssbo.cbuf_index &&\n"
        "                               desc.cbuf_offset == ssbo.cbuf_offset &&\n"
        "                               desc.indirect_offset == ~0U;\n"
        "                    })};\n"
        "                if (holder == info.storage_buffers_descriptors.end()) {\n"
        "                    base = Constant(U64, ~u64{0});\n"
        "                } else {\n"
        "                    const size_t holder_index{static_cast<size_t>(\n"
        "                        std::distance(info.storage_buffers_descriptors.begin(), holder))};\n"
        "                    const Id holder_low{OpCompositeExtract(U32[1], words, 0U)};\n"
        "                    const Id byte{OpIAdd(\n"
        "                        U32[1],\n"
        "                        OpBitwiseAnd(U32[1], holder_low,\n"
        "                                     Const(static_cast<u32>(profile.min_ssbo_alignment - 1))),\n"
        "                        Const(ssbo.indirect_offset))};\n"
        "                    const Id word_index{OpShiftRightLogical(U32[1], byte, Const(2U))};\n"
        "                    const Id storage{ssbos[holder_index].U32};\n"
        "                    const auto load_word{[&](Id word) {\n"
        "                        return OpLoad(U32[1], OpAccessChain(storage_types.U32.element,\n"
        "                                                            storage, zero, word));\n"
        "                    }};\n"
        "                    const Id pointer_words{OpCompositeConstruct(\n"
        "                        U32[2], load_word(word_index),\n"
        "                        load_word(OpIAdd(U32[1], word_index, Const(1U))))};\n"
        "                    base = OpBitwiseAnd(U64, OpBitcast(U64, pointer_words),\n"
        "                                        Constant(U64, ssbo_align_mask));\n"
        "                }\n"
        "            }\n"
        "            OpStore(nx2_base_vars[index], base);\n"
        "        }\n"
        "        OpReturn();\n"
        "        OpFunctionEnd();\n"
        "        nx2_init_global_bases = init_func;\n"
        "    }\n",
    ),
])
print("hoisted global memory bases installed")
