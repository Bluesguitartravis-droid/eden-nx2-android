#!/usr/bin/env python3
"""Profile: count every Scheduler::Finish (submit + wait for the GPU) by its call site and
report the top sites every 10 s, next to "Scheduler per second". The buffer and texture
cache runtimes forward their caller's location so the report names the real call site."""
from pathlib import Path
import sys

root = Path(sys.argv[1]).resolve() / "src/video_core"

def patch(path, old, new, marker):
    p = root / path
    t = p.read_text()
    if marker in t:
        return
    if old not in t:
        raise SystemExit(f"{path}: anchor not found: {old[:60]!r}")
    p.write_text(t.replace(old, new, 1))

# Scheduler: per-site Finish counts and time.
patch("renderer_vulkan/vk_scheduler.cpp",
      "void ReportSubmitCounts() {",
      """std::map<std::string, std::pair<u64, u64>> g_nx2_finish_sites;

void CountFinishSite(const std::source_location& location, u64 ns) {
    std::string_view name{location.file_name()};
    if (const auto slash = name.find_last_of('/'); slash != std::string_view::npos) {
        name.remove_prefix(slash + 1);
    }
    std::scoped_lock lock{g_profile_end_mutex};
    auto& entry = g_nx2_finish_sites[fmt::format("{}:{}", name, location.line())];
    ++entry.first;
    entry.second += ns;
}

void ReportSubmitCounts() {""",
      "g_nx2_finish_sites")
patch("renderer_vulkan/vk_scheduler.cpp",
      """    std::sort(ends.rbegin(), ends.rend());""",
      """    std::vector<std::tuple<u64, u64, std::string>> nx2_finishes;
    {
        std::scoped_lock lock{g_profile_end_mutex};
        for (const auto& [site, entry] : g_nx2_finish_sites) {
            nx2_finishes.emplace_back(entry.first, entry.second, site);
        }
        g_nx2_finish_sites.clear();
    }
    std::sort(nx2_finishes.rbegin(), nx2_finishes.rend());
    for (size_t i = 0; i < nx2_finishes.size() && i < 10; ++i) {
        LOG_INFO(Render_Vulkan, "NX2 finish {:.0f}/s ({:.1f} ms/s) at {}",
                 static_cast<double>(std::get<0>(nx2_finishes[i])) / seconds,
                 static_cast<double>(std::get<1>(nx2_finishes[i])) / 1e6 / seconds,
                 std::get<2>(nx2_finishes[i]));
    }
    std::sort(ends.rbegin(), ends.rend());""",
      "NX2 finish {:.0f}/s")
patch("renderer_vulkan/vk_scheduler.cpp",
      """        g_profile_finishes.fetch_add(1, std::memory_order_relaxed);
        g_profile_finish_ns.fetch_add(VideoCommon::Nx2Profile::Now() - start,
                                      std::memory_order_relaxed);""",
      """        const u64 nx2_ns = VideoCommon::Nx2Profile::Now() - start;
        g_profile_finishes.fetch_add(1, std::memory_order_relaxed);
        g_profile_finish_ns.fetch_add(nx2_ns, std::memory_order_relaxed);
        CountFinishSite(location, nx2_ns);""",
      "CountFinishSite(location, nx2_ns)")
p = root / "renderer_vulkan/vk_scheduler.cpp"
t = p.read_text()
for inc in ("#include <tuple>\n", "#include <source_location>\n"):
    if inc not in t:
        t = inc + t
p.write_text(t)

# Runtimes forward the caller's location.
for hdr, src, cls in (("renderer_vulkan/vk_buffer_cache.h", "renderer_vulkan/vk_buffer_cache.cpp", "BufferCacheRuntime"),
                      ("renderer_vulkan/vk_texture_cache.h", "renderer_vulkan/vk_texture_cache.cpp", "TextureCacheRuntime")):
    patch(hdr, "    void Finish();",
          "    void Finish(std::source_location nx2_location = std::source_location::current());",
          "nx2_location")
    h = root / hdr
    ht = h.read_text()
    if "#include <source_location>" not in ht:
        ht = ht.replace("#pragma once\n", "#pragma once\n\n#include <source_location>\n", 1)
        h.write_text(ht)
    patch(src, f"void {cls}::Finish() {{\n    scheduler.Finish();",
          f"void {cls}::Finish(std::source_location nx2_location) {{\n    scheduler.Finish({{}}, {{}}, nx2_location);",
          f"{cls}::Finish(std::source_location")
print("finish-site profiling applied")
