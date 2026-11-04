#!/usr/bin/env python3
"""Android test patch for the memory-map exhaustion seen on the Odin 3 Ultra.

Every Eglantine crash on Android aborted in scudo::reportMapError: the process ran out of
memory mappings (vm.max_map_count, 65530). Newer Eglantine backs GPU-only regions lazily, one
mapping per 4 MiB block; SX2A v0.3.2 backed them whole. Android builds cannot set the
EDEN_NX2_GPU_ONLY_LAZY switch, so default it off there, and log the process's mapping count
(NX2_MAPS) so a test shows how close it gets to the limit."""
from pathlib import Path
import re
import sys

root = Path(sys.argv[1]).resolve()
gsv = root / "src/core/hle/service/nx2_gsv.h"
t = gsv.read_text()

# 1. GPU-only regions backed whole by default on Android.
old = '''            const char* value = std::getenv("EDEN_NX2_GPU_ONLY_LAZY");
            return value == nullptr || value[0] != '0';'''
new = '''            const char* value = std::getenv("EDEN_NX2_GPU_ONLY_LAZY");
#ifdef __ANDROID__
            // Lazy backing costs a mapping per block; Android's map limit ran out (NX2_MAPS).
            return value != nullptr && value[0] == '1';
#else
            return value == nullptr || value[0] != '0';
#endif'''
if old not in t:
    raise SystemExit("LazyGpuOnlyRegions shape changed")
t = t.replace(old, new, 1)

# 2. Mapping-count helper at file scope.
helper = '''// Lines in /proc/self/maps: the process's memory mappings, which vm.max_map_count limits.
inline size_t Nx2CountProcessMappings() {
    std::FILE* f = std::fopen("/proc/self/maps", "r");
    if (f == nullptr) {
        return 0;
    }
    size_t lines = 0;
    char buffer[16384];
    size_t n;
    while ((n = std::fread(buffer, 1, sizeof(buffer), f)) > 0) {
        for (size_t i = 0; i < n; ++i) {
            lines += buffer[i] == '\\n';
        }
    }
    std::fclose(f);
    return lines;
}

'''
anchor = "inline void TipcReplyWords(HLERequestContext& ctx, std::span<const u32> words) {"
if anchor not in t:
    raise SystemExit("TipcReplyWords anchor missing")
t = t.replace(anchor, helper + anchor, 1)

# 3. Log it with every 600-present summary.
present = '''            if (++m_stats.presents == 600) {
                LOG_INFO(Service,
                         "nx2:vi2 last 600 presents: '''
if present not in t:
    raise SystemExit("present stats anchor missing")
t = t.replace(present, '''            if (++m_stats.presents == 600) {
                LOG_INFO(Service, "NX2_MAPS count={} at present", Nx2CountProcessMappings());
                LOG_INFO(Service,
                         "nx2:vi2 last 600 presents: ''', 1)

# 4. And every 256 GPU-only backings, which happen while a level loads and frames stall.
m = re.search(r"(    std::optional<MappedBacking> AllocateGpuOnlyBacking\(u64 size, bool log_failure, bool clear\) \{\n)", t)
if not m:
    raise SystemExit("AllocateGpuOnlyBacking anchor missing")
t = t[:m.end()] + '''        static std::atomic<u32> nx2_backings{0};
        if ((nx2_backings.fetch_add(1, std::memory_order_relaxed) & 255) == 0) {
            LOG_INFO(Service, "NX2_MAPS count={} at GPU-only backing {:#x}", Nx2CountProcessMappings(), size);
        }
''' + t[m.end():]

if "#include <cstdio>" not in t:
    t = t.replace("#include <cstdlib>\n", "#include <cstdlib>\n#include <cstdio>\n", 1)
gsv.write_text(t)
print("Android map budget patch applied")
