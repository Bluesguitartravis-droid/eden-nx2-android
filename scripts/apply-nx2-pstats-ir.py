#!/usr/bin/env python3
"""EDEN_NX2_PSTATS_IR=1 (measurement aid, with EDEN_NX2_PSTATS_HASH): besides the statistics, ask
the driver for each matching graphics pipeline's internal representations (Turnip: the final NIR
and the ir3 disassembly) and write them to files/nx2_ir/<pipeline>-<executable>-<n>.txt, where
the Lab's read_file can fetch them. Used to see why a shader is slow (spills, private arrays,
long dependency chains), not only that it is."""
from pathlib import Path
import sys

src = Path(sys.argv[1]).resolve() / "src/video_core"


def replace(path, old, new):
    text = path.read_text()
    if text.count(old) != 1:
        sys.exit(f"pstats-ir: {path.name}: expected 1 match, found {text.count(old)}:\n{old}")
    path.write_text(text.replace(old, new))


wh = src / "vulkan_common/vulkan_wrapper.h"
replace(wh, """    PFN_vkGetPipelineExecutableStatisticsKHR vkGetPipelineExecutableStatisticsKHR{};
""", """    PFN_vkGetPipelineExecutableStatisticsKHR vkGetPipelineExecutableStatisticsKHR{};
    PFN_vkGetPipelineExecutableInternalRepresentationsKHR
        vkGetPipelineExecutableInternalRepresentationsKHR{};
""")
replace(wh, """    std::vector<VkPipelineExecutableStatisticKHR> GetPipelineExecutableStatisticsKHR(
        VkPipeline pipeline, u32 executable_index) const;
""", """    std::vector<VkPipelineExecutableStatisticKHR> GetPipelineExecutableStatisticsKHR(
        VkPipeline pipeline, u32 executable_index) const;

    /// NX2: (name, text) of each internal representation (needs the capture flag at creation)
    std::vector<std::pair<std::string, std::string>> GetPipelineExecutableInternalRepresentationsKHR(
        VkPipeline pipeline, u32 executable_index) const;
""")

wc = src / "vulkan_common/vulkan_wrapper.cpp"
replace(wc, """    X(vkGetPipelineExecutableStatisticsKHR);
""", """    X(vkGetPipelineExecutableStatisticsKHR);
    X(vkGetPipelineExecutableInternalRepresentationsKHR);
""")
replace(wc, """void Device::UpdateDescriptorSets(Span<VkWriteDescriptorSet> writes,
                                  Span<VkCopyDescriptorSet> copies) const noexcept {""",
        """std::vector<std::pair<std::string, std::string>>
Device::GetPipelineExecutableInternalRepresentationsKHR(VkPipeline pipeline,
                                                        u32 executable_index) const {
    std::vector<std::pair<std::string, std::string>> result;
    if (dld->vkGetPipelineExecutableInternalRepresentationsKHR == nullptr) {
        return result;
    }
    const VkPipelineExecutableInfoKHR executable_info{
        .sType = VK_STRUCTURE_TYPE_PIPELINE_EXECUTABLE_INFO_KHR,
        .pNext = nullptr,
        .pipeline = pipeline,
        .executableIndex = executable_index,
    };
    u32 num{};
    dld->vkGetPipelineExecutableInternalRepresentationsKHR(handle, &executable_info, &num, nullptr);
    std::vector<VkPipelineExecutableInternalRepresentationKHR> representations(num);
    for (auto& representation : representations) {
        representation.sType = VK_STRUCTURE_TYPE_PIPELINE_EXECUTABLE_INTERNAL_REPRESENTATION_KHR;
        representation.pNext = nullptr;
        representation.dataSize = 0;
        representation.pData = nullptr;
    }
    dld->vkGetPipelineExecutableInternalRepresentationsKHR(handle, &executable_info, &num,
                                                           representations.data());
    std::vector<std::string> data(representations.size());
    for (size_t i = 0; i < representations.size(); ++i) {
        data[i].resize(representations[i].dataSize);
        representations[i].pData = data[i].data();
    }
    dld->vkGetPipelineExecutableInternalRepresentationsKHR(handle, &executable_info, &num,
                                                           representations.data());
    for (size_t i = 0; i < representations.size(); ++i) {
        std::string text = std::move(data[i]);
        while (!text.empty() && text.back() == '\\0') {
            text.pop_back();
        }
        result.emplace_back(representations[i].name, std::move(text));
    }
    return result;
}

void Device::UpdateDescriptorSets(Span<VkWriteDescriptorSet> writes,
                                  Span<VkCopyDescriptorSet> copies) const noexcept {""")

gp = src / "renderer_vulkan/vk_graphics_pipeline.cpp"
replace(gp, """        flags |= VK_PIPELINE_CREATE_CAPTURE_STATISTICS_BIT_KHR; // NX2_PSTATS
""", """        flags |= VK_PIPELINE_CREATE_CAPTURE_STATISTICS_BIT_KHR; // NX2_PSTATS
        if (std::getenv("EDEN_NX2_PSTATS_IR") != nullptr) {
            flags |= VK_PIPELINE_CREATE_CAPTURE_INTERNAL_REPRESENTATIONS_BIT_KHR;
        }
""")

ps = src / "renderer_vulkan/nx2_pipeline_stats.h"
replace(ps, """#include <cstdlib>
#include <exception>
""", """#include <cstdlib>
#include <exception>
#include <filesystem>
#include <fstream>
""")
replace(ps, """            LOG_INFO(Render_Vulkan, "NX2_PSTATS {} [{}]{}", label, properties[executable].name,
                     line.empty() ? std::string{" no statistics"} : line);
""", """            LOG_INFO(Render_Vulkan, "NX2_PSTATS {} [{}]{}", label, properties[executable].name,
                     line.empty() ? std::string{" no statistics"} : line);
            if (std::getenv("EDEN_NX2_PSTATS_IR") != nullptr) {
                const std::filesystem::path directory{
                    "/storage/emulated/0/Android/data/dev.eden.eden_emulator/files/nx2_ir"};
                std::error_code error;
                std::filesystem::create_directories(directory, error);
                std::string stem{label};
                for (char& c : stem) {
                    if (c == ' ' || c == ':' || c == '/') {
                        c = '_';
                    }
                }
                const auto representations{
                    device.GetLogical().GetPipelineExecutableInternalRepresentationsKHR(pipeline,
                                                                                        executable)};
                for (size_t index = 0; index < representations.size(); ++index) {
                    const auto path = directory / fmt::format("{}-{}-{}.txt", stem.substr(0, 120),
                                                              executable, index);
                    std::ofstream file{path};
                    file << "# " << properties[executable].name << " / "
                         << representations[index].first << "\\n"
                         << representations[index].second;
                    LOG_INFO(Render_Vulkan, "NX2_PSTATS_IR wrote {} ({} bytes)", path.string(),
                             representations[index].second.size());
                }
            }
""")
print("pstats-ir: EDEN_NX2_PSTATS_IR added")
