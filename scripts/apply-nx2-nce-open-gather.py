#!/usr/bin/env python3
"""NCE open pages were almost never gathered: GPU::InvalidateGPUCache skips ApplyCollectedWrites
when no core's GPUDirtyMemoryManager may have a pending write (a check made for ~900 calls a frame
with nothing collected). On NCE the managers stay empty (Dynarmic fills them), so the CPU writes
on pages opened at write faults reached the GPU only when something else happened to collect a
write. Ocarina's world rendered white on NCE with open pages on (248, 251), and fine with them
off (249), where a write fault invalidates the page at once.

Memory publishes its open page count in Common::Nx2NceStats::open_count; InvalidateGPUCache
gathers when it is non-zero."""
from pathlib import Path
import sys

src = Path(sys.argv[1]).resolve() / "src"


def replace(path, old, new, count=1):
    text = path.read_text()
    if text.count(old) != count:
        sys.exit(f"nce-open-gather: {path.name}: expected {count} match(es), found {text.count(old)}:\n{old}")
    path.write_text(text.replace(old, new))


replace(src / "common/nx2_nce_stats.h", "inline std::atomic<bool> open_pages{true};\n",
        "inline std::atomic<bool> open_pages{true};\n"
        "// Pages opened at NCE write faults, not yet gathered (GPU::InvalidateGPUCache must gather).\n"
        "inline std::atomic<std::uint64_t> open_count{};\n")

memory = src / "core/memory.cpp"
replace(memory, "            nx2_open_count.store(nx2_open_pages.size(), std::memory_order_relaxed);\n",
        "            nx2_open_count.store(nx2_open_pages.size(), std::memory_order_relaxed);\n"
        "            Common::Nx2NceStats::open_count.store(nx2_open_pages.size(),\n"
        "                                                 std::memory_order_release);\n")
replace(memory, "        nx2_open_count.store(0, std::memory_order_relaxed);\n",
        "        nx2_open_count.store(0, std::memory_order_relaxed);\n"
        "        Common::Nx2NceStats::open_count.store(0, std::memory_order_release);\n")

gpu = src / "video_core/gpu.cpp"
replace(gpu, """        if (std::ranges::none_of(managers, &Core::GPUDirtyMemoryManager::MayHavePending)) {""",
        """        // NX2: pages opened at NCE write faults are gathered here too
        // (scripts/apply-nx2-nce-open-gather.py).
        if (std::ranges::none_of(managers, &Core::GPUDirtyMemoryManager::MayHavePending) &&
            Common::Nx2NceStats::open_count.load(std::memory_order_acquire) == 0) {""")
text = gpu.read_text()
if '#include "common/nx2_nce_stats.h"' not in text:
    first = text.index("#include ")
    gpu.write_text(text[:first] + '#include "common/nx2_nce_stats.h"\n' + text[first:])
print("nce-open-gather: open NCE pages are gathered by InvalidateGPUCache")
