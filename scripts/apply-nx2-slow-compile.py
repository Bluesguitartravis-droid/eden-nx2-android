#!/usr/bin/env python3
"""Ocarina: as its AV1 intro starts, both pipeline build threads sat at 100% for 20-60 s on
Qualcomm's driver and on Turnip, which then crashed in its compiler. Upstream builds two compute
shaders with VK_PIPELINE_CREATE_DISABLE_OPTIMIZATION_BIT on AMD for the same stall (#200).

- BuildsUnoptimized: Adreno drivers too, and any stage hash listed in EDEN_NX2_UNOPTIMIZED
  (comma-separated hex); graphics pipelines consult it per stage.
- Upstream's build trace (EDEN_NX2_PIPELINE_BUILD_TRACE=<file>) also goes to the log when no
  file is given, so the last BEGIN without an END in a crash log names the shader."""
from pathlib import Path
import sys

root = Path(sys.argv[1]).resolve() / "src/video_core/renderer_vulkan"

h = root / "pipeline_optimization.h"
t = h.read_text()
old = """    return driver_id == VK_DRIVER_ID_AMD_PROPRIETARY &&
           std::ranges::find(amd_proprietary, shader_hash) != amd_proprietary.end();"""
new = """    // NX2: Adreno's compilers (Qualcomm's and Turnip) stall or crash on Ocarina's shaders too;
    // EDEN_NX2_UNOPTIMIZED adds more (comma-separated hex hashes).
    static const std::vector<u64> nx2_extra = [] {
        std::vector<u64> out;
        if (const char* v = std::getenv("EDEN_NX2_UNOPTIMIZED")) {
            std::string s{v};
            size_t pos = 0;
            while (pos < s.size()) {
                const size_t end = s.find(',', pos);
                const std::string item =
                    s.substr(pos, end == std::string::npos ? std::string::npos : end - pos);
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
    t = t.replace("#include <array>\n",
                  "#include <array>\n#include <cstdlib>\n#include <string>\n#include <vector>\n", 1)
    h.write_text(t)

g = root / "vk_graphics_pipeline.cpp"
t = g.read_text()
old = """    const bool unoptimized = (flags & VK_PIPELINE_CREATE_DISABLE_OPTIMIZATION_BIT) != 0;
    const auto trace_begin = std::chrono::steady_clock::now();
    TracePipelineBuild("BEGIN", "graphics\""""
new = """    for (const u64 nx2_stage_hash : key.unique_hashes) {
        if (nx2_stage_hash != 0 && BuildsUnoptimized(device.GetDriverID(), nx2_stage_hash)) {
            flags |= VK_PIPELINE_CREATE_DISABLE_OPTIMIZATION_BIT;
        }
    }
    const bool unoptimized = (flags & VK_PIPELINE_CREATE_DISABLE_OPTIMIZATION_BIT) != 0;
    const auto trace_begin = std::chrono::steady_clock::now();
    TracePipelineBuild("BEGIN", "graphics\""""
if "nx2_stage_hash" not in t:
    if old not in t:
        raise SystemExit("graphics pipeline trace shape changed")
    t = t.replace(old, new, 1)
    if "pipeline_optimization.h" not in t:
        anchor = '#include "video_core/renderer_vulkan/vk_graphics_pipeline.h"\n'
        if anchor not in t:
            raise SystemExit("graphics pipeline include not found")
        t = t.replace(anchor, anchor + '#include "video_core/renderer_vulkan/pipeline_optimization.h"\n', 1)
    g.write_text(t)

tr = root / "pipeline_build_trace.h"
t = tr.read_text()
old = """    if (file == nullptr) {
        return;
    }"""
new = """    if (file == nullptr) {
        // NX2 test builds: into the log instead, starts and builds over 500 ms only.
        if (elapsed_ms >= 0.0 && elapsed_ms < 500.0) {
            return;
        }
        std::string stages;
        for (size_t stage = 0; stage < stage_hashes.size(); ++stage) {
            if (stage_hashes[stage] != 0) {
                stages += fmt::format(" s{}:{:016x}", stage, stage_hashes[stage]);
            }
        }
        LOG_INFO(Render_Vulkan, "NX2 pipeline build {} {} pipeline={:016x}{}{}{}", phase, kind,
                 key_hash, unoptimized ? " unoptimized" : "", stages,
                 elapsed_ms >= 0.0 ? fmt::format(" ms={:.0f}", elapsed_ms) : std::string{});
        return;
    }"""
if "NX2 pipeline build" not in t:
    if old not in t:
        raise SystemExit("pipeline build trace shape changed")
    t = t.replace(old, new, 1)
    t = t.replace('#include "common/common_types.h"\n',
                  '#include <string>\n#include <fmt/format.h>\n#include "common/common_types.h"\n'
                  '#include "common/logging.h"\n', 1)
    tr.write_text(t)
print("slow pipeline builds: Adreno unoptimized list, EDEN_NX2_UNOPTIMIZED, build trace in the log")
