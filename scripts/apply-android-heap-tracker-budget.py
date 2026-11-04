#!/usr/bin/env python3
"""Android: leave more of vm.max_map_count to the rest of the process.

HeapTracker keeps up to max_map_count - 20000 guest heap mappings resident (45530 on the Odin 3
Ultra). On Android the rest of the process (Vulkan driver, shader compiler, scudo) needs more
than that reserve while a level loads, and every crash there failed a memory mapping (scudo
reportMapError, std::bad_alloc in dynarmic). Keep the tracker to max_map_count - 45000 on
Android and log each eviction with the process's real mapping count (NX2_HEAP)."""
from pathlib import Path
import sys

root = Path(sys.argv[1]).resolve()
f = root / "src/common/heap_tracker.cpp"
t = f.read_text()

old = """    // Allow 20000 maps for other code and to account for split inaccuracy.
    return std::max<s64>(value - 20000, 0);"""
new = """#ifdef __ANDROID__
    // Android: the Vulkan driver, its shader compiler and scudo need far more than 20000 maps
    // while a Switch 2 level loads; running out aborts the process in malloc.
    return std::max<s64>(value - 45000, 0);
#else
    // Allow 20000 maps for other code and to account for split inaccuracy.
    return std::max<s64>(value - 20000, 0);
#endif"""
if old not in t:
    raise SystemExit("heap tracker budget shape changed")
t = t.replace(old, new, 1)

helper = """
// Lines in /proc/self/maps: the mappings vm.max_map_count limits.
static size_t Nx2ProcessMapCount() {
    std::FILE* file = std::fopen("/proc/self/maps", "r");
    if (file == nullptr) {
        return 0;
    }
    size_t lines = 0;
    char buffer[16384];
    size_t n;
    while ((n = std::fread(buffer, 1, sizeof(buffer), file)) > 0) {
        for (size_t i = 0; i < n; ++i) {
            lines += buffer[i] == '\\n';
        }
    }
    std::fclose(file);
    return lines;
}
"""
anchor = "} // namespace\n\nHeapTracker::HeapTracker("
if anchor not in t:
    raise SystemExit("heap tracker namespace anchor missing")
t = t.replace(anchor, helper + anchor, 1)

old_rebuild = """    std::size_t const desired_count = (std::min)(m_resident_map_count, m_max_resident_map_count) / 2;
    std::size_t const evict_count = m_resident_map_count - desired_count;"""
new_rebuild = old_rebuild + """
    LOG_INFO(HW_Memory, "NX2_HEAP rebuild resident={} max={} evict={} process_maps={}",
             m_resident_map_count, m_max_resident_map_count, evict_count, Nx2ProcessMapCount());"""
if old_rebuild not in t:
    raise SystemExit("heap tracker rebuild shape changed")
t = t.replace(old_rebuild, new_rebuild, 1)

if "#include <cstdio>" not in t:
    t = t.replace("#include <fstream>\n", "#include <cstdio>\n#include <fstream>\n", 1)
f.write_text(t)
print("Android heap tracker budget applied")
