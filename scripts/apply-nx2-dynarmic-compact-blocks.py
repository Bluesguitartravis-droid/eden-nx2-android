#!/usr/bin/env python3
"""Dynarmic (ARM64 backend): keep each compiled block's tables in compact lists.

Build 197 on the Odin, Ocarina of Time on Dynarmic (it does not boot on NCE yet): the emulator's
C++ heap held ~8.6 GB (NX2_HEAP large 2.4 GB + small ~6.2 GB) and Android closed Eden near 17-19 GB.
Most of it was Dynarmic's per-block bookkeeping:

  ~2.15 GB  EmittedBlockInfo::block_relocations  (hash map: target -> branch sites)
  ~2.13 GB  EmittedBlockInfo::fastmem_patch_info (hash map: code offset -> fastmem patch)
  ~0.79 GB  AddressSpace::block_references       (hash set per target block)

Every compiled block (six AddressSpaces, one per core) carried two boost flat hash maps, and a
flat hash map holds at least one 15-slot group however few entries it has; most blocks have a
handful. They are now plain vectors with linear lookup (Nx2SmallMap), trimmed to size when the
block is stored, and block_references keeps a vector of referring blocks per target (Link adds
each (target, block) pair once; stale entries were already skipped by RelinkForDescriptor).
Lookups stay rare: block_relocations is searched when a block is relinked, fastmem_patch_info
when a fastmem access faults.

Usage: python3 scripts/apply-nx2-dynarmic-compact-blocks.py <upstream-root>
"""
from pathlib import Path
import sys

arm64 = Path(sys.argv[1]) / "src/dynarmic/src/dynarmic/backend/arm64"
MARK = "NX2 compact block tables"


def patch(name, edits):
    path = arm64 / name
    text = path.read_text()
    if MARK in text:
        print(f"{name}: already patched")
        return
    for old, new in edits:
        if text.count(old) != 1:
            raise SystemExit(f"{name}: anchor found {text.count(old)} times:\n{old}")
        text = text.replace(old, new, 1)
    path.write_text(text)
    print(f"{name}: patched")


patch("emit_arm64.h", [
    ("#include <array>\n#include <cstddef>\n#include <memory>\n#include <vector>\n",
     "#include <algorithm>\n#include <array>\n#include <cstddef>\n#include <iterator>\n#include <memory>\n"
     "#include <tuple>\n#include <utility>\n#include <vector>\n"),
    ("struct EmittedBlockInfo {\n"
     "    CodePtr entry_point;\n"
     "    std::size_t size;\n"
     "    std::vector<Relocation> relocations;\n"
     "    ::Common::unordered_map<IR::LocationDescriptor, std::vector<BlockRelocation>> block_relocations;\n"
     "    ::Common::unordered_map<std::ptrdiff_t, FastmemPatchInfo> fastmem_patch_info;\n"
     "};\n",
     "// " + MARK + ": a map kept as a vector of pairs with linear lookup. Each compiled block\n"
     "// holds two of these with a few entries; a hash map costs at least a 15-slot group each\n"
     "// (~4.3 GB over Ocarina's blocks on six cores).\n"
     "template<typename K, typename V>\n"
     "struct Nx2SmallMap {\n"
     "    using value_type = std::pair<K, V>;\n"
     "    using iterator = typename std::vector<value_type>::iterator;\n"
     "    using const_iterator = typename std::vector<value_type>::const_iterator;\n"
     "\n"
     "    std::vector<value_type> items;\n"
     "\n"
     "    iterator begin() { return items.begin(); }\n"
     "    iterator end() { return items.end(); }\n"
     "    const_iterator begin() const { return items.begin(); }\n"
     "    const_iterator end() const { return items.end(); }\n"
     "    std::size_t size() const { return items.size(); }\n"
     "    bool empty() const { return items.empty(); }\n"
     "    void clear() { items.clear(); }\n"
     "\n"
     "    iterator find(const K& key) {\n"
     "        return std::find_if(items.begin(), items.end(), [&](const value_type& item) { return item.first == key; });\n"
     "    }\n"
     "    const_iterator find(const K& key) const {\n"
     "        return std::find_if(items.begin(), items.end(), [&](const value_type& item) { return item.first == key; });\n"
     "    }\n"
     "    V& operator[](const K& key) {\n"
     "        if (const auto iter = find(key); iter != items.end()) {\n"
     "            return iter->second;\n"
     "        }\n"
     "        return items.emplace_back(key, V{}).second;\n"
     "    }\n"
     "    template<typename... Args>\n"
     "    std::pair<iterator, bool> emplace(const K& key, Args&&... args) {\n"
     "        if (const auto iter = find(key); iter != items.end()) {\n"
     "            return {iter, false};\n"
     "        }\n"
     "        items.emplace_back(std::piecewise_construct, std::forward_as_tuple(key),\n"
     "                           std::forward_as_tuple(std::forward<Args>(args)...));\n"
     "        return {std::prev(items.end()), true};\n"
     "    }\n"
     "    void shrink_to_fit() { items.shrink_to_fit(); }\n"
     "};\n"
     "\n"
     "struct EmittedBlockInfo {\n"
     "    CodePtr entry_point;\n"
     "    std::size_t size;\n"
     "    std::vector<Relocation> relocations;\n"
     "    Nx2SmallMap<IR::LocationDescriptor, std::vector<BlockRelocation>> block_relocations;\n"
     "    Nx2SmallMap<std::ptrdiff_t, FastmemPatchInfo> fastmem_patch_info;\n"
     "\n"
     "    /// Trim the vectors to their size before the block is stored.\n"
     "    void ShrinkToFit() {\n"
     "        relocations.shrink_to_fit();\n"
     "        for (auto& entry : block_relocations) {\n"
     "            entry.second.shrink_to_fit();\n"
     "        }\n"
     "        block_relocations.shrink_to_fit();\n"
     "        fastmem_patch_info.shrink_to_fit();\n"
     "    }\n"
     "};\n"),
])

patch("address_space.h", [(
    "    ::Common::unordered_map<IR::LocationDescriptor, ::Common::unordered_set<CodePtr>> block_references;\n",
    "    // " + MARK + ": the blocks that branch to a target, as a list (Link adds each pair once).\n"
    "    ::Common::unordered_map<IR::LocationDescriptor, std::vector<CodePtr>> block_references;\n",
)])

patch("address_space.cpp", [
    ("    EmittedBlockInfo block_info = EmitArm64(code, std::move(block), emit_config, fastmem_manager);\n",
     "    EmittedBlockInfo block_info = EmitArm64(code, std::move(block), emit_config, fastmem_manager);\n"
     "    // " + MARK + ": the stored copy holds exactly what the block needs.\n"
     "    block_info.ShrinkToFit();\n"),
    ("        block_references[target_descriptor].insert(block_info.entry_point);\n",
     "        block_references[target_descriptor].push_back(block_info.entry_point);\n"),
])
print("Dynarmic block tables are compact")
