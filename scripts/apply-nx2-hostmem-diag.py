#!/usr/bin/env python3
"""Explain fastmem mapping failures (Ocarina of Time on Android).

Ocarina logs ~30k "mmap: Invalid argument" from HostMemory::Map/Unmap, and
hangs on NCE. Log the ranges of the first failures (NX2_HOSTMEM), and skip
mmap calls whose range AdjustMap clipped to nothing (mmap of length 0 is
always EINVAL).
"""
from pathlib import Path
import sys

f = Path(sys.argv[1]) / "src/common/host_memory.cpp"
t = f.read_text()
if "NX2_HOSTMEM" in t:
    print("hostmem diag already applied"); sys.exit(0)

map_old = '''        int flags = (fd >= 0 ? MAP_SHARED : MAP_PRIVATE) | MAP_FIXED;
        void* ret = mmap(virtual_base + virtual_offset, length, prot_flags, flags, fd, host_offset);
        ASSERT_MSG(ret != MAP_FAILED, "mmap: {} {}", strerror(errno), fd);
'''
map_new = '''        int flags = (fd >= 0 ? MAP_SHARED : MAP_PRIVATE) | MAP_FIXED;
        if (length == 0) {
            static std::atomic<int> clipped{0};
            if (clipped++ < 8) {
                LOG_WARNING(HW_Memory, "NX2_HOSTMEM map clipped to nothing: asked {:#x} vbase={} vmap={} vsize={:#x}",
                            intended_offset, fmt::ptr(virtual_base), fmt::ptr(virtual_map_base), virtual_size);
            }
            return;
        }
        void* ret = mmap(virtual_base + virtual_offset, length, prot_flags, flags, fd, host_offset);
        if (ret == MAP_FAILED) {
            const int err = errno;
            static std::atomic<int> failures{0};
            if (failures++ < 24) {
                LOG_CRITICAL(HW_Memory,
                             "NX2_HOSTMEM map failed: {} fd={} voff={:#x} asked={:#x} len={:#x} hoff={:#x} "
                             "backing={:#x} vsize={:#x} vbase={} prot={:#x}",
                             strerror(err), fd, virtual_offset, intended_offset, length, host_offset,
                             backing_size, virtual_size, fmt::ptr(virtual_base), prot_flags);
            }
        }
'''
unmap_old = '''        void* ret = mmap(merged_pointer, merged_size, PROT_NONE, MAP_PRIVATE | MAP_ANONYMOUS | MAP_FIXED, -1, 0);
        ASSERT_MSG(ret != MAP_FAILED, "mmap: {}", strerror(errno));
'''
unmap_new = '''        if (merged_size == 0) {
            return;
        }
        void* ret = mmap(merged_pointer, merged_size, PROT_NONE, MAP_PRIVATE | MAP_ANONYMOUS | MAP_FIXED, -1, 0);
        if (ret == MAP_FAILED) {
            const int err = errno;
            static std::atomic<int> failures{0};
            if (failures++ < 24) {
                LOG_CRITICAL(HW_Memory, "NX2_HOSTMEM unmap failed: {} ptr={} size={:#x} voff={:#x} len={:#x}",
                             strerror(err), fmt::ptr(merged_pointer), merged_size, virtual_offset, length);
            }
        }
'''
for old, new, what in ((map_old, map_new, "Map"), (unmap_old, unmap_new, "Unmap")):
    if old not in t:
        raise SystemExit(f"host_memory.cpp: {what} mmap block shape changed")
    t = t.replace(old, new, 1)
if "#include <atomic>" not in t:
    t = t.replace("#include <", "#include <atomic>\n#include <", 1)
f.write_text(t)
print("NX2_HOSTMEM diagnostics installed")
