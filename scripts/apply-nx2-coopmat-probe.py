#!/usr/bin/env python3
"""Probe: log the device's extensions and its VK_KHR_cooperative_matrix shapes once. Tensor-core
(HMMA) work is emulated with plain shader math; if the Adreno driver offers cooperative
matrices, that work could run on the GPU's own matrix path instead."""
from pathlib import Path
import sys

f = Path(sys.argv[1]).resolve() / "src/video_core/vulkan_common/vulkan_device.cpp"
t = f.read_text()
anchor = """    for (const VkExtensionProperties& property : extension_properties) {
        supported_extensions.insert(property.extensionName);
    }
"""
probe = anchor + """
    // NX2: extension list and cooperative matrix shapes, once.
    if (static bool nx2_probed = false; !nx2_probed) {
        nx2_probed = true;
        std::string nx2_exts;
        for (const auto& name : supported_extensions) {
            nx2_exts += name;
            nx2_exts += ' ';
        }
        LOG_INFO(Render_Vulkan, "NX2 device extensions ({}): {}", supported_extensions.size(),
                 nx2_exts);
        if (supported_extensions.contains("VK_KHR_cooperative_matrix")) {
            const auto get = reinterpret_cast<PFN_vkGetPhysicalDeviceCooperativeMatrixPropertiesKHR>(
                dld.vkGetInstanceProcAddr(instance,
                                          "vkGetPhysicalDeviceCooperativeMatrixPropertiesKHR"));
            u32 count = 0;
            if (get && get(physical, &count, nullptr) == VK_SUCCESS) {
                std::vector<VkCooperativeMatrixPropertiesKHR> props(
                    count, VkCooperativeMatrixPropertiesKHR{
                               .sType = VK_STRUCTURE_TYPE_COOPERATIVE_MATRIX_PROPERTIES_KHR});
                get(physical, &count, props.data());
                for (const auto& p : props) {
                    LOG_INFO(Render_Vulkan,
                             "NX2 cooperative matrix M={} N={} K={} A={} B={} C={} R={} "
                             "saturating={} scope={}",
                             p.MSize, p.NSize, p.KSize, static_cast<int>(p.AType),
                             static_cast<int>(p.BType), static_cast<int>(p.CType),
                             static_cast<int>(p.ResultType), p.saturatingAccumulation,
                             static_cast<int>(p.scope));
                }
            } else {
                LOG_INFO(Render_Vulkan, "NX2 cooperative matrix: extension listed, query failed");
            }
        } else {
            LOG_INFO(Render_Vulkan, "NX2 cooperative matrix: not supported");
        }
    }
"""
if "NX2 device extensions" not in t:
    if anchor not in t:
        raise SystemExit("extension loop not found")
    t = t.replace(anchor, probe, 1)
    f.write_text(t)
print("cooperative matrix probe applied")
