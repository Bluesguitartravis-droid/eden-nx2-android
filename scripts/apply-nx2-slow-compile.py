#!/usr/bin/env python3
"""Ocarina: as its AV1 intro starts, both pipeline build threads sat at 100% for 30-60 s on
Qualcomm's driver and on Turnip (Turnip then crashed in its compiler). Upstream found the same
two compute shaders taking 6 and 18 minutes on AMD (#200) and builds them with
VK_PIPELINE_CREATE_DISABLE_OPTIMIZATION_BIT there. Do the same on Qualcomm and Turnip, accept
more hashes from EDEN_NX2_UNOPTIMIZED (comma-separated hex), and log every compute build that
takes over 500 ms plus the start of each one, so a build that never ends is named in the log."""
from pathlib import Path
import sys

root = Path(sys.argv[1]).resolve() / "src/video_core/renderer_vulkan"

h = root / "pipeline_optimization.h"
t = h.read_text()
old = """    return driver_id == VK_DRIVER_ID_AMD_PROPRIETARY &&
           std::ranges::find(amd_proprietary, shader_hash) != amd_proprietary.end();"""
new = """    // NX2: the same two shaders stall Adreno's compilers (Qualcomm's and Turnip) for a minute
    // or crash them; EDEN_NX2_UNOPTIMIZED adds more (comma-separated hex hashes).
    static const std::vector<u64> nx2_extra = [] {
        std::vector<u64> out;
        if (const char* v = std::getenv("EDEN_NX2_UNOPTIMIZED")) {
            std::string s{v};
            size_t pos = 0;
            while (pos < s.size()) {
                const size_t end = s.find(',', pos);
                const std::string item = s.substr(pos, end == std::string::npos ? end : end - pos);
                if (!item.empty()) {
                    out.push_back(std::strtoull(item.c_str(), nullptr, 16));
                }
                if (end == std::string::npos) {
                    break;
                }
                pos = end + 1;
            }
        }
        return out;
    }();
    if (std::ranges::find(nx2_extra, shader_hash) != nx2_extra.end()) {
        return true;
    }
    const bool adreno = driver_id == VK_DRIVER_ID_QUALCOMM_PROPRIETARY ||
                        driver_id == VK_DRIVER_ID_MESA_TURNIP;
    return (driver_id == VK_DRIVER_ID_AMD_PROPRIETARY || adreno) &&
           std::ranges::find(amd_proprietary, shader_hash) != amd_proprietary.end();"""
if "EDEN_NX2_UNOPTIMIZED" not in t:
    if old not in t:
        raise SystemExit("BuildsUnoptimized shape changed")
    t = t.replace(old, new, 1)
    t = t.replace("#include <array>\n", "#include <array>\n#include <cstdlib>\n#include <string>\n#include <vector>\n", 1)
    h.write_text(t)

c = root / "vk_compute_pipeline.cpp"
t = c.read_text()
old = """        try {
            pipeline = device.GetLogical().CreateComputePipeline(compute_ci, *pipeline_cache);
        } catch (const vk::Exception& exception) {"""
new = """        const auto nx2_build_start = std::chrono::steady_clock::now();
        LOG_INFO(Render_Vulkan, "NX2 compute build start {:016x}{}", shader_hash,
                 (flags & VK_PIPELINE_CREATE_DISABLE_OPTIMIZATION_BIT) != 0 ? " (unoptimized)" : "");
        try {
            pipeline = device.GetLogical().CreateComputePipeline(compute_ci, *pipeline_cache);
            const double nx2_ms = std::chrono::duration<double, std::milli>(
                std::chrono::steady_clock::now() - nx2_build_start).count();
            if (nx2_ms > 500.0) {
                LOG_WARNING(Render_Vulkan, "NX2 compute build slow {:016x}: {:.0f} ms", shader_hash,
                            nx2_ms);
            }
        } catch (const vk::Exception& exception) {"""
if "NX2 compute build start" not in t:
    if old not in t:
        raise SystemExit("compute pipeline creation shape changed")
    t = t.replace(old, new, 1)
    if "#include <chrono>" not in t:
        t = "#include <chrono>\n" + t
    c.write_text(t)
print("slow compute shaders built unoptimized on Adreno; compute builds logged")
