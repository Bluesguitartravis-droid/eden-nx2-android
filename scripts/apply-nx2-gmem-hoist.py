#!/usr/bin/env python3
"""Pointer loads: work out the candidate buffers' base addresses once per invocation.

Build 164's frame trace of Ocarina of Time (Turnip, Kokiri Forest, GPU 60-90 ms a frame):
vertex shaders that load through pointers (NX2_GMEM: 72 loads, 7 candidate buffers, 5 of them
found through a pointer read from another buffer) take about 11 ms of every frame, one draw up to
11 ms on its own. Every pointer load called a helper that recomputed each candidate's base: a
constant buffer read per candidate plus two storage buffer reads for each indirect one, so about
17 memory reads before the load itself, 72 times a vertex. Banana Native measured the same shape
at 146-171 ms on Turnip against 2.5 ms on Qualcomm's driver.

For native programs (native_global_bounds) that write no storage buffer, the bases are now read on
the first pointer load of an invocation and kept in private variables for the others. Constant
buffers cannot change during a draw, and the pointer holders are storage buffers the program does
not write. EDEN_NX2_GMEM_HOIST=0 restores the old code.
Usage: python3 scripts/apply-nx2-gmem-hoist.py <upstream-root>
"""
from pathlib import Path
import sys

path = Path(sys.argv[1]) / "src/shader_recompiler/backend/spirv/spirv_emit_context.cpp"
text = path.read_text()
MARK = "NX2 hoisted global memory bases"
if MARK in text:
    print("already patched")
    raise SystemExit(0)


def replace_once(old, new):
    global text
    if text.count(old) != 1:
        raise SystemExit(f"spirv_emit_context.cpp: anchor found {text.count(old)} times:\n{old}")
    text = text.replace(old, new, 1)


replace_once(
    "    using DefPtr = Id StorageDefinitions::*;\n"
    "    const Id zero{u32_zero_value};\n",
    "    using DefPtr = Id StorageDefinitions::*;\n"
    "    const Id zero{u32_zero_value};\n"
    "    // " + MARK + ": one set of private variables shared by every helper.\n"
    "    static const bool nx2_hoist_enabled{[] {\n"
    "        const char* const value{std::getenv(\"EDEN_NX2_GMEM_HOIST\")};\n"
    "        return value == nullptr || value[0] != '0';\n"
    "    }()};\n"
    "    const bool nx2_hoist{nx2_hoist_enabled && info.native_global_bounds &&\n"
    "                         !info.storage_buffers_descriptors.empty() &&\n"
    "                         std::ranges::none_of(info.storage_buffers_descriptors,\n"
    "                                              [](const auto& desc) { return desc.is_written; })};\n"
    "    std::vector<Id> nx2_base_vars;\n"
    "    Id nx2_bases_ready{};\n"
    "    if (nx2_hoist) {\n"
    "        const Id u64_private{TypePointer(spv::StorageClass::Private, U64)};\n"
    "        for (size_t index = 0; index < info.storage_buffers_descriptors.size(); ++index) {\n"
    "            nx2_base_vars.push_back(AddGlobalVariable(u64_private, spv::StorageClass::Private));\n"
    "        }\n"
    "        nx2_bases_ready = AddGlobalVariable(TypePointer(spv::StorageClass::Private, U1),\n"
    "                                            spv::StorageClass::Private, ConstantFalse(U1));\n"
    "        if (profile.supported_spirv >= 0x00010400) {\n"
    "            interfaces.insert(interfaces.end(), nx2_base_vars.begin(), nx2_base_vars.end());\n"
    "            interfaces.push_back(nx2_bases_ready);\n"
    "        }\n"
    "    }\n")

replace_once(
    "            std::vector<Id> bases(num_buffers);\n"
    "            Id best_index{Const(~0U)};\n"
    "            Id best_distance{Constant(U64, ~u64{0})};\n"
    "            for (size_t index = 0; index < num_buffers; ++index) {\n"
    "                bases[index] = base_address(index);\n",
    "            std::vector<Id> bases(num_buffers);\n"
    "            std::vector<Id> nx2_loaded(num_buffers);\n"
    "            if (nx2_hoist) {\n"
    "                // " + MARK + ": computed by the first pointer load of the invocation.\n"
    "                const Id compute_label{OpLabel()};\n"
    "                const Id merge_label{OpLabel()};\n"
    "                OpSelectionMerge(merge_label, spv::SelectionControlMask::MaskNone);\n"
    "                OpBranchConditional(OpLoad(U1, nx2_bases_ready), merge_label, compute_label);\n"
    "                AddLabel(compute_label);\n"
    "                for (size_t index = 0; index < num_buffers; ++index) {\n"
    "                    OpStore(nx2_base_vars[index], base_address(index));\n"
    "                }\n"
    "                OpStore(nx2_bases_ready, ConstantTrue(U1));\n"
    "                OpBranch(merge_label);\n"
    "                AddLabel(merge_label);\n"
    "                for (size_t index = 0; index < num_buffers; ++index) {\n"
    "                    nx2_loaded[index] = OpLoad(U64, nx2_base_vars[index]);\n"
    "                }\n"
    "            }\n"
    "            Id best_index{Const(~0U)};\n"
    "            Id best_distance{Constant(U64, ~u64{0})};\n"
    "            for (size_t index = 0; index < num_buffers; ++index) {\n"
    "                bases[index] = nx2_hoist ? nx2_loaded[index] : base_address(index);\n")

if "#include <cstdlib>\n" not in text:
    text = text.replace("#include <algorithm>\n", "#include <algorithm>\n#include <cstdlib>\n", 1)
path.write_text(text)
print("hoisted global memory bases installed")
