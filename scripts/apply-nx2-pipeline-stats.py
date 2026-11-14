#!/usr/bin/env python3
"""EDEN_NX2_PIPELINE_STATS=1: log the GPU driver's own statistics for every pipeline it builds.

Build 168/169 traces of Ocarina of Time (Kokiri Forest, Turnip V39 and Qualcomm's driver alike):
three compute programs (s6:e40f5efb5480e692, s6:03bf0ffc7d2d0bed, s6:9302bc0d6d1d9bc1; about
15,000 IR instructions, 128 threads, 5-6.5 KiB of shared memory) take 13-17 ms of a 44 ms GPU
frame. Whether that is register pressure (few waves per core), spilling or plain instruction count
decides what can be done about it, and only the driver knows.

With the switch on, the device enables VK_KHR_pipeline_executable_properties, pipelines are built
with VK_PIPELINE_CREATE_CAPTURE_STATISTICS_BIT_KHR, and each one logs one line per executable:

  NX2_PSTATS compute s6:<hash> [<executable>] Instruction Count=... Max Waves Per Core=... ...
  NX2_PSTATS graphics <pipeline> s1:<hash> ... [<executable>] ...

Turnip reports registers, waves per core, spills/fills, NOPs and stall cycles; Qualcomm's driver
reports what it reports. Off by default (statistics capture can slow pipeline builds). Pipelines
loaded from the driver's cache may come without statistics: the line then says so.
Usage: python3 scripts/apply-nx2-pipeline-stats.py <upstream-root>
"""
from pathlib import Path
import sys

root = Path(sys.argv[1]).resolve() / "src/video_core"
vk = root / "renderer_vulkan"
MARK = "NX2_PSTATS"

(vk / "nx2_pipeline_stats.h").write_text(r'''// NX2_PSTATS: the driver's statistics for each pipeline, with EDEN_NX2_PIPELINE_STATS=1.
#pragma once

#include <cstdlib>
#include <exception>
#include <string>
#include <string_view>

#include <fmt/format.h>

#include "common/logging.h"
#include "video_core/vulkan_common/vulkan_device.h"
#include "video_core/vulkan_common/vulkan_wrapper.h"

namespace Vulkan {

inline bool Nx2PipelineStatsEnabled() {
    static const bool enabled{[] {
        const char* const value{std::getenv("EDEN_NX2_PIPELINE_STATS")};
        return value != nullptr && value[0] == '1';
    }()};
    return enabled;
}

inline void Nx2LogPipelineStats(const Device& device, VkPipeline pipeline, std::string_view label) {
    if (!Nx2PipelineStatsEnabled() || !device.IsKhrPipelineExecutablePropertiesEnabled() ||
        pipeline == VK_NULL_HANDLE) {
        return;
    }
    try {
        const auto properties{device.GetLogical().GetPipelineExecutablePropertiesKHR(pipeline)};
        if (properties.empty()) {
            LOG_INFO(Render_Vulkan, "NX2_PSTATS {} no statistics (cached pipeline?)", label);
            return;
        }
        for (u32 executable = 0; executable < static_cast<u32>(properties.size()); ++executable) {
            const auto statistics{
                device.GetLogical().GetPipelineExecutableStatisticsKHR(pipeline, executable)};
            std::string line;
            for (const auto& statistic : statistics) {
                switch (statistic.format) {
                case VK_PIPELINE_EXECUTABLE_STATISTIC_FORMAT_BOOL32_KHR:
                    line += fmt::format(" {}={}", statistic.name, statistic.value.b32 ? 1 : 0);
                    break;
                case VK_PIPELINE_EXECUTABLE_STATISTIC_FORMAT_INT64_KHR:
                    line += fmt::format(" {}={}", statistic.name, statistic.value.i64);
                    break;
                case VK_PIPELINE_EXECUTABLE_STATISTIC_FORMAT_UINT64_KHR:
                    line += fmt::format(" {}={}", statistic.name, statistic.value.u64);
                    break;
                case VK_PIPELINE_EXECUTABLE_STATISTIC_FORMAT_FLOAT64_KHR:
                    line += fmt::format(" {}={:.2f}", statistic.name, statistic.value.f64);
                    break;
                default:
                    break;
                }
            }
            LOG_INFO(Render_Vulkan, "NX2_PSTATS {} [{}]{}", label, properties[executable].name,
                     line.empty() ? std::string{" no statistics"} : line);
        }
    } catch (const std::exception& error) {
        LOG_INFO(Render_Vulkan, "NX2_PSTATS {} unavailable: {}", label, error.what());
    }
}

} // namespace Vulkan
''')


def patch(path, edits, include_after=None):
    text = path.read_text()
    if MARK in text:
        print(f"{path.name}: already patched")
        return
    for old, new in edits:
        if text.count(old) != 1:
            raise SystemExit(f"{path.name}: anchor found {text.count(old)} times:\n{old}")
        text = text.replace(old, new, 1)
    if include_after:
        anchor, line = include_after
        if text.count(anchor) < 1:
            raise SystemExit(f"{path.name}: include anchor missing")
        text = text.replace(anchor, anchor + line, 1)
    path.write_text(text)
    print(f"{path.name}: patched")


# The device enables the extension for the switch as for the shader feedback setting.
patch(root / "vulkan_common/vulkan_device.cpp", [(
    "    // VK_KHR_pipeline_executable_properties\n"
    "    if (Settings::values.renderer_shader_feedback.GetValue()) {\n",
    "    // VK_KHR_pipeline_executable_properties (also for EDEN_NX2_PIPELINE_STATS=1, NX2_PSTATS)\n"
    "    if (Settings::values.renderer_shader_feedback.GetValue() || [] {\n"
    "            const char* const value{std::getenv(\"EDEN_NX2_PIPELINE_STATS\")};\n"
    "            return value != nullptr && value[0] == '1';\n"
    "        }()) {\n",
)], include_after=("#include <algorithm>\n", "#include <cstdlib>\n"))

patch(vk / "vk_compute_pipeline.cpp", [
    (
        "        VkPipelineCreateFlags flags{};\n"
        "        if (device.IsKhrPipelineExecutablePropertiesEnabled() && Settings::values.renderer_debug.GetValue()) {\n",
        "        VkPipelineCreateFlags flags{};\n"
        "        if (device.IsKhrPipelineExecutablePropertiesEnabled() && Nx2PipelineStatsEnabled()) {\n"
        "            flags |= VK_PIPELINE_CREATE_CAPTURE_STATISTICS_BIT_KHR; // NX2_PSTATS\n"
        "        }\n"
        "        if (device.IsKhrPipelineExecutablePropertiesEnabled() && Settings::values.renderer_debug.GetValue()) {\n",
    ),
    (
        "        if (pipeline_statistics) {\n"
        "            pipeline_statistics->Collect(device, *pipeline);\n"
        "        }\n",
        "        if (pipeline_statistics) {\n"
        "            pipeline_statistics->Collect(device, *pipeline);\n"
        "        }\n"
        "        if (Nx2PipelineStatsEnabled()) {\n"
        "            Nx2LogPipelineStats(device, *pipeline, fmt::format(\"compute s6:{:016x}\", shader_hash));\n"
        "        }\n",
    ),
], include_after=('#include "video_core/renderer_vulkan/vk_compute_pipeline.h"\n',
                  '#include "video_core/renderer_vulkan/nx2_pipeline_stats.h"\n'))

patch(vk / "vk_graphics_pipeline.cpp", [
    (
        "    VkPipelineCreateFlags flags{};\n"
        "    if (device.IsKhrPipelineExecutablePropertiesEnabled() && Settings::values.renderer_debug.GetValue()) {\n",
        "    VkPipelineCreateFlags flags{};\n"
        "    if (device.IsKhrPipelineExecutablePropertiesEnabled() && Nx2PipelineStatsEnabled()) {\n"
        "        flags |= VK_PIPELINE_CREATE_CAPTURE_STATISTICS_BIT_KHR; // NX2_PSTATS\n"
        "    }\n"
        "    if (device.IsKhrPipelineExecutablePropertiesEnabled() && Settings::values.renderer_debug.GetValue()) {\n",
    ),
    (
        "    TracePipelineBuild(\"END\", \"graphics\", key.Hash(), key.unique_hashes, unoptimized,\n",
        "    if (Nx2PipelineStatsEnabled()) {\n"
        "        std::string nx2_label{fmt::format(\"graphics {:016x}\", key.Hash())};\n"
        "        for (size_t nx2_stage = 0; nx2_stage < key.unique_hashes.size(); ++nx2_stage) {\n"
        "            if (key.unique_hashes[nx2_stage] != 0) {\n"
        "                nx2_label += fmt::format(\" s{}:{:016x}\", nx2_stage, key.unique_hashes[nx2_stage]);\n"
        "            }\n"
        "        }\n"
        "        Nx2LogPipelineStats(device, *pipeline, nx2_label);\n"
        "    }\n"
        "    TracePipelineBuild(\"END\", \"graphics\", key.Hash(), key.unique_hashes, unoptimized,\n",
    ),
], include_after=('#include "video_core/renderer_vulkan/vk_graphics_pipeline.h"\n',
                  '#include "video_core/renderer_vulkan/nx2_pipeline_stats.h"\n'))
print("NX2_PSTATS pipeline statistics installed (EDEN_NX2_PIPELINE_STATS=1)")
