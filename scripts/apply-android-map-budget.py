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

# 2. Mapping summary helper at file scope: count, mapped bytes, largest gap, top address.
helper = '''// /proc/self/maps summary: mapping count (vm.max_map_count limits it), bytes mapped, the
// largest unmapped gap below the top mapping and the top address (Android arm64 has a 39-bit
// user address space, 512 GiB). A failing mmap with few mappings means address space ran out.
inline std::string Nx2MapsSummary() {
    std::FILE* f = std::fopen("/proc/self/maps", "r");
    if (f == nullptr) {
        return "unavailable";
    }
    size_t count = 0;
    unsigned long long mapped = 0, largest_gap = 0, previous_end = 0, top = 0;
    char line[512];
    while (std::fgets(line, sizeof(line), f) != nullptr) {
        unsigned long long start = 0, end = 0;
        if (std::sscanf(line, "%llx-%llx", &start, &end) != 2 || start >= (1ULL << 48)) {
            continue;
        }
        ++count;
        mapped += end - start;
        if (previous_end != 0 && start > previous_end) {
            largest_gap = (std::max)(largest_gap, start - previous_end);
        }
        previous_end = (std::max)(previous_end, end);
        top = (std::max)(top, end);
    }
    std::fclose(f);
    char out[160];
    std::snprintf(out, sizeof(out), "count=%zu mapped=%.1fGiB largest_gap=%.1fGiB top=%#llx", count,
                  mapped / 1073741824.0, largest_gap / 1073741824.0, top);
    return out;
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
                LOG_INFO(Service, "NX2_MAPS {} at present", Nx2MapsSummary());
                LOG_INFO(Service,
                         "nx2:vi2 last 600 presents: ''', 1)

# 4. And every 256 GPU-only backings, which happen while a level loads and frames stall.
m = re.search(r"(    std::optional<MappedBacking> AllocateGpuOnlyBacking\(u64 size, bool log_failure, bool clear\) \{\n)", t)
if not m:
    raise SystemExit("AllocateGpuOnlyBacking anchor missing")
t = t[:m.end()] + '''        static std::atomic<u32> nx2_backings{0};
        if ((nx2_backings.fetch_add(1, std::memory_order_relaxed) & 63) == 0) {
            LOG_INFO(Service, "NX2_MAPS {} at GPU-only backing {:#x}", Nx2MapsSummary(), size);
        }
''' + t[m.end():]

if "#include <cstdio>" not in t:
    t = t.replace("#include <cstdlib>\n", "#include <cstdlib>\n#include <cstdio>\n", 1)
gsv.write_text(t)
print("Android map budget patch applied")
