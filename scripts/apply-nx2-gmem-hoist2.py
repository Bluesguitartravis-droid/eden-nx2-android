#!/usr/bin/env python3
"""Pointer loads read their buffers' base addresses once per invocation (Lichii's routed helpers).

Builds 169-191 hoisted these bases on the old single-helper base (apply-nx2-gmem-hoist.py); since
Lichii's #1807 rewrite (one helper set per route) that patch skips itself, so every pointer load
again recomputes its candidate's base: a constant buffer read, and for a pointer kept in another
buffer two storage buffer reads and 64-bit math, before the load itself.

Build 213, Ocarina at Lon Lon Ranch: the costliest draw (s1:b809b6c2+s5:d9ba002d, ~9 ms for 5
draws) has a 6,440-instruction vertex shader on Turnip that spills (STP 119 / LDP 141) against
331 instructions for its position-only variant. NX2_GMEM s1:b809b6c2: 72 loads, 7 candidates,
5 of them indirect, native pointers, 5 routes of one buffer each.

For graphics stages with native pointers that write no storage buffer and use no global writes or
atomics (the old patch's conditions; Turnip's compiler crashed on hoisted compute programs), a
function the entry point calls first stores every candidate's base in a private variable, and the
routed helpers read those. Constant buffers do not change during a draw and the pointer holders
are buffers the program does not write. EDEN_NX2_GMEM_HOIST=0 restores per-load bases.

Usage: apply-nx2-gmem-hoist2.py <upstream source root>
"""
import pathlib
import sys

spirv = pathlib.Path(sys.argv[1]) / "src/shader_recompiler/backend/spirv"
MARK = "NX2 hoisted global memory bases"


def patch(name, edits):
    path = spirv / name
    text = path.read_text()
    if MARK in text:
        print(f"{name}: already patched")
        return
    for old, new in edits:
        if text.count(old) != 1:
            sys.exit(f"{name}: anchor found {text.count(old)} times:\n{old}")
        text = text.replace(old, new, 1)
    if name.endswith(".cpp") and "#include <cstdlib>\n" not in text:
        text = text.replace("#include <algorithm>\n", "#include <algorithm>\n#include <cstdlib>\n", 1)
    path.write_text(text)
    print(f"{name}: patched")


if "global_memory_functions" not in (spirv / "spirv_emit_context.h").read_text():
    print("Skipped: this base has the single helper set (apply-nx2-gmem-hoist.py covers it)")
    sys.exit(0)

patch("spirv_emit_context.h", [(
    "    std::vector<GlobalMemoryFunctions> global_memory_functions;\n",
    "    std::vector<GlobalMemoryFunctions> global_memory_functions;\n"
    "    /// " + MARK + ": called first by the entry point when set.\n"
    "    Id nx2_init_global_bases{};\n",
)])

patch("emit_spirv.cpp", [(
    "    for (const IR::AbstractSyntaxNode& node : program.syntax_list) {\n"
    "        switch (node.type) {\n"
    "        case IR::AbstractSyntaxNode::Type::Block: {\n"
    "            const Id label{node.data.block->Definition<Id>()};\n"
    "            if (current_block) {\n"
    "                ctx.OpBranch(label);\n"
    "            }\n"
    "            current_block = node.data.block;\n"
    "            ctx.AddLabel(label);\n",
    "    // " + MARK + ": only the first block calls the init function (a call at the top of a\n"
    "    // merge block would come before its OpPhi: invalid SPIR-V, builds 165-168).\n"
    "    bool nx2_bases_called{};\n"
    "    for (const IR::AbstractSyntaxNode& node : program.syntax_list) {\n"
    "        switch (node.type) {\n"
    "        case IR::AbstractSyntaxNode::Type::Block: {\n"
    "            const Id label{node.data.block->Definition<Id>()};\n"
    "            if (current_block) {\n"
    "                ctx.OpBranch(label);\n"
    "            }\n"
    "            current_block = node.data.block;\n"
    "            ctx.AddLabel(label);\n"
    "            if (!nx2_bases_called && Sirit::ValidId(ctx.nx2_init_global_bases)) {\n"
    "                ctx.OpFunctionCall(ctx.void_id, ctx.nx2_init_global_bases);\n"
    "            }\n"
    "            nx2_bases_called = true;\n",
)])

INIT_BASE = r'''
        const u64 ssbo_align_mask{~(profile.min_ssbo_alignment - 1U)};
        for (size_t index = 0; index < info.storage_buffers_descriptors.size(); ++index) {
            const auto& ssbo{info.storage_buffers_descriptors[index]};
            const Id words{EmitGetCbufU32x2(*this, IR::Value{ssbo.cbuf_index},
                                           IR::Value{ssbo.cbuf_offset})};
            Id base{};
            if (ssbo.indirect_offset == ~0U) {
                base = OpBitwiseAnd(U64, OpBitcast(U64, words), Constant(U64, ssbo_align_mask));
            } else {
                const auto holder{std::ranges::find_if(
                    info.storage_buffers_descriptors, [&ssbo](const auto& desc) {
                        return desc.cbuf_index == ssbo.cbuf_index &&
                               desc.cbuf_offset == ssbo.cbuf_offset && desc.indirect_offset == ~0U;
                    })};
                if (holder == info.storage_buffers_descriptors.end()) {
                    base = Constant(U64, ~u64{0});
                } else {
                    const size_t holder_index{static_cast<size_t>(
                        std::distance(info.storage_buffers_descriptors.begin(), holder))};
                    const Id holder_low{OpCompositeExtract(U32[1], words, 0U)};
                    const Id byte{OpIAdd(
                        U32[1],
                        OpBitwiseAnd(U32[1], holder_low,
                                     Const(static_cast<u32>(profile.min_ssbo_alignment - 1))),
                        Const(ssbo.indirect_offset))};
                    const Id word_index{OpShiftRightLogical(U32[1], byte, Const(2U))};
                    const Id storage{ssbos[holder_index].U32};
                    const auto load_word{[&](Id word) {
                        return OpLoad(U32[1], OpAccessChain(storage_types.U32.element, storage,
                                                            zero, word));
                    }};
                    const Id pointer_words{OpCompositeConstruct(
                        U32[2], load_word(word_index), load_word(OpIAdd(U32[1], word_index, Const(1U))))};
                    base = OpBitwiseAnd(U64, OpBitcast(U64, pointer_words),
                                        Constant(U64, ssbo_align_mask));
                }
            }
            OpStore(nx2_base_vars[index], base);
        }
'''

patch("spirv_emit_context.cpp", [
    (
        "    using DefPtr = Id StorageDefinitions::*;\n"
        "    const Id zero{u32_zero_value};\n",
        "    using DefPtr = Id StorageDefinitions::*;\n"
        "    const Id zero{u32_zero_value};\n"
        "    // " + MARK + ": every candidate's base in a private variable, stored once by a\n"
        "    // function the entry point calls first.\n"
        "    static const bool nx2_hoist_enabled{[] {\n"
        "        const char* const value{std::getenv(\"EDEN_NX2_GMEM_HOIST\")};\n"
        "        return value == nullptr || value[0] != '0';\n"
        "    }()};\n"
        "    const bool nx2_hoist{\n"
        "        nx2_hoist_enabled && !profile.select_global_memory_buffer &&\n"
        "        stage != Stage::Compute && stage != Stage::Task && stage != Stage::Mesh &&\n"
        "        info.native_global_bounds && !info.storage_buffers_descriptors.empty() &&\n"
        "        std::ranges::none_of(info.storage_buffers_descriptors,\n"
        "                             [](const auto& desc) { return desc.is_written; }) &&\n"
        "        std::ranges::none_of(program.blocks, [](const IR::Block* block) {\n"
        "            return std::ranges::any_of(block->Instructions(), [](const IR::Inst& inst) {\n"
        "                const IR::Opcode op{inst.GetOpcode()};\n"
        "                return (op >= IR::Opcode::WriteGlobalU8 && op <= IR::Opcode::WriteGlobal128) ||\n"
        "                       (op >= IR::Opcode::GlobalAtomicIAdd32 &&\n"
        "                        op <= IR::Opcode::GlobalAtomicMaxF32x2);\n"
        "            });\n"
        "        })};\n"
        "    std::vector<Id> nx2_base_vars;\n"
        "    if (nx2_hoist) {\n"
        "        const Id u64_private{TypePointer(spv::StorageClass::Private, U64)};\n"
        "        for (size_t index = 0; index < info.storage_buffers_descriptors.size(); ++index) {\n"
        "            nx2_base_vars.push_back(AddGlobalVariable(u64_private, spv::StorageClass::Private));\n"
        "        }\n"
        "        if (profile.supported_spirv >= 0x00010400) {\n"
        "            interfaces.insert(interfaces.end(), nx2_base_vars.begin(), nx2_base_vars.end());\n"
        "        }\n"
        "    }\n",
    ),
    (
        "        const auto base_address{[&](size_t index) {\n"
        "            const auto& ssbo{info.storage_buffers_descriptors[index]};\n",
        "        const auto base_address{[&](size_t index) {\n"
        "            if (nx2_hoist) {\n"
        "                return OpLoad(U64, nx2_base_vars[index]);\n"
        "            }\n"
        "            const auto& ssbo{info.storage_buffers_descriptors[index]};\n",
    ),
    (
        "        if (use.compare_exchange) {\n"
        "            functions->compare_exchange_u32 = define_compare_exchange();\n"
        "        }\n"
        "    }\n"
        "}\n",
        "        if (use.compare_exchange) {\n"
        "            functions->compare_exchange_u32 = define_compare_exchange();\n"
        "        }\n"
        "    }\n"
        "    if (nx2_hoist) {\n"
        "        // " + MARK + ": the same base computation as search's base_address.\n"
        "        const Id init_func{OpFunction(void_id, spv::FunctionControlMask::MaskNone,\n"
        "                                      TypeFunction(void_id))};\n"
        "        AddLabel();\n" + INIT_BASE +
        "        OpReturn();\n"
        "        OpFunctionEnd();\n"
        "        nx2_init_global_bases = init_func;\n"
        "    }\n"
        "}\n",
    ),
])
print("hoisted global memory bases installed (routed helpers)")
