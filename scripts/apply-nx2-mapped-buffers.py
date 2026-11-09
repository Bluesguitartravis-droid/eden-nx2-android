#!/usr/bin/env python3
"""Adreno shares memory between CPU and GPU, as Apple GPUs do. On MoltenVK the buffer
cache already allocates its buffers host-visible ("Stream"), so a CPU read of GPU-written
buffer memory just waits for the buffer's last use and copies from the mapping. Everywhere
else each such read records a GPU copy into a staging buffer, submits, and waits for the
whole GPU (Scheduler::Finish): Donkey Kong Bananza on NCE does that ~1,700 times a second
(buffer_cache.h DownloadBufferMemory), 530-770 ms of every second.

Allocate the cache's (non-sparse) buffers as Stream on Qualcomm/Turnip too.
EDEN_NX2_MAPPED_BUFFERS=0 turns it off. The mapped-read path is counted and logged as
"NX2 mapped download" next to the scheduler stats; what a mapped read waits for is
apply-nx2-download-write-tick.py's."""
from pathlib import Path
import sys

root = Path(sys.argv[1]).resolve() / "src/video_core"

def patch(rel, old, new, marker):
    p = root / rel
    t = p.read_text()
    if marker in t:
        return
    if old not in t:
        raise SystemExit(f"{rel}: anchor not found: {old[:70]!r}")
    p.write_text(t.replace(old, new, 1))

patch("renderer_vulkan/vk_buffer_cache.cpp",
      """    return memory_allocator.CreateBuffer(
        buffer_ci, device.IsMoltenVK() ? MemoryUsage::Stream : MemoryUsage::DeviceLocal);""",
      """    static const bool nx2_mapped = [&device] {
        const char* v = std::getenv("EDEN_NX2_MAPPED_BUFFERS");
        if (v && v[0] == '0') {
            return false;
        }
        const auto id = device.GetDriverID();
        const bool unified = id == VK_DRIVER_ID_QUALCOMM_PROPRIETARY ||
                             id == VK_DRIVER_ID_MESA_TURNIP;
        LOG_INFO(Render_Vulkan, "NX2 buffer cache: host-mapped buffers {}",
                 unified ? "on (unified memory)" : "off");
        return unified;
    }();
    return memory_allocator.CreateBuffer(
        buffer_ci, (device.IsMoltenVK() || nx2_mapped) ? MemoryUsage::Stream
                                                       : MemoryUsage::DeviceLocal);""",
      "EDEN_NX2_MAPPED_BUFFERS")
p = root / "renderer_vulkan/vk_buffer_cache.cpp"
t = p.read_text()
if "#include <cstdlib>" not in t:
    p.write_text("#include <cstdlib>\n" + t)

# Every submission ends with a host-read barrier, so a fence wait makes its writes visible
# to the CPU through the mapping.
sc = root / "renderer_vulkan/vk_scheduler.cpp"
st = sc.read_text()
if "NX2: make this submission's writes visible" not in st:
    start = st.index("u64 Scheduler::SubmitExecution(")
    end_at = st.index("        cmdbuf.End();\n", start)
    st = st[:end_at] + """        {
            // NX2: make this submission's writes visible to host reads of mapped buffers.
            const VkMemoryBarrier nx2_host_read{
                .sType = VK_STRUCTURE_TYPE_MEMORY_BARRIER,
                .pNext = nullptr,
                .srcAccessMask = VK_ACCESS_MEMORY_WRITE_BIT,
                .dstAccessMask = VK_ACCESS_HOST_READ_BIT,
            };
            cmdbuf.PipelineBarrier(VK_PIPELINE_STAGE_ALL_COMMANDS_BIT, VK_PIPELINE_STAGE_HOST_BIT,
                                   0, nx2_host_read);
        }
""" + st[end_at:]
    sc.write_text(st)

# Counters shared by the buffer cache (template header) and the scheduler report.
patch("nx2_profile.h",
      "} // namespace VideoCommon::Nx2Profile",
      """inline std::atomic<u64> g_nx2_mapped_downloads{};
inline std::atomic<u64> g_nx2_mapped_download_ns{};

} // namespace VideoCommon::Nx2Profile""",
      "g_nx2_mapped_downloads")
p = root / "nx2_profile.h"
t = p.read_text()
if "#include <atomic>" not in t:
    t = t.replace("#pragma once\n", "#pragma once\n\n#include <atomic>\n", 1)
    p.write_text(t)

patch("renderer_vulkan/vk_scheduler.cpp",
      """    std::vector<std::tuple<u64, u64, std::string>> nx2_finishes;""",
      """    LOG_INFO(Render_Vulkan, "NX2 mapped download {:.0f}/s waiting {:.1f} ms/s",
             static_cast<double>(VideoCommon::Nx2Profile::g_nx2_mapped_downloads.exchange(0)) / seconds,
             static_cast<double>(VideoCommon::Nx2Profile::g_nx2_mapped_download_ns.exchange(0)) /
                 1e6 / seconds);
    std::vector<std::tuple<u64, u64, std::string>> nx2_finishes;""",
      "NX2 mapped download")
print("host-mapped buffer cache buffers applied")
