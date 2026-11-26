#!/usr/bin/env python3
"""Ocarina's GPU-written dispatch sizes (Lichii's Nx2ListIndirect) on devices whose compute grid
limit is below 2^31 groups wide. The Adreno 830 reports 65535x65535x65535, so Lichii's check turned
the path off on the Odin, and the GPU thread's front waited for the culling to run and its copy to
come back instead (the "list writers" waits). Build 278 measured +9-11 % at the drawbridge.

279: on by default; EDEN_NX2_LIST_INDIRECT_ANY_LIMIT=0 (live, switches file) turns it off. On such a
device the slot's dispatch width is copied as 16 bits, like height and depth already are (the
slot's upper halves stay 0 from its fill), so an indirect dispatch can never pass the device's
65535 limit (in spec for any value the guest writes; a width over 65535, which the direct path
would skip, wraps instead; culling counts are far below it)."""
from pathlib import Path
import sys

src = Path(sys.argv[1]).resolve() / "src/video_core/renderer_vulkan"


def patch(name, pairs):
    path = src / name
    text = path.read_text()
    for old, new in pairs:
        if text.count(old) != 1:
            sys.exit(f"apply-nx2-list-indirect-any-limit: expected one {old[:70]!r} in {name}, "
                     f"found {text.count(old)}")
        text = text.replace(old, new)
    path.write_text(text)


patch("nx2_list_slots.h", [(
    '''class Nx2ListSlots {
  public:''',
    '''/// The device dispatches at most 65535 groups wide (an Adreno): the slot's width is copied as
/// 16 bits (EDEN_NX2_LIST_INDIRECT_ANY_LIMIT). Set by RasterizerVulkan::Nx2ListSlotsSupported.
inline std::atomic<bool> g_nx2_narrow_grid_width{false};

class Nx2ListSlots {
  public:''')])
if "#include <atomic>" not in (src / "nx2_list_slots.h").read_text():
    patch("nx2_list_slots.h", [("#pragma once\n", "#pragma once\n\n#include <atomic>\n")])

patch("nx2_list_slots.cpp", [(
    '''        copies.push_back(VkBufferCopy{src_offset, base + DispatchOffset, 4});
        copies.push_back(VkBufferCopy{src_offset + 4, base + DispatchOffset + 4, 2});''',
    '''        // On a device that dispatches at most 65535 groups wide, the width as 16 bits too, so
        // the indirect dispatch stays within the device's limits for any value.
        const VkDeviceSize width_bytes =
            g_nx2_narrow_grid_width.load(std::memory_order_relaxed) ? 2 : 4;
        copies.push_back(VkBufferCopy{src_offset, base + DispatchOffset, width_bytes});
        copies.push_back(VkBufferCopy{src_offset + 4, base + DispatchOffset + 4, 2});''')])

patch("vk_rasterizer.cpp", [(
    '''    if (static std::atomic<bool> logged{}; !fits && !logged.exchange(true)) {
        LOG_WARNING(Render_Vulkan,
                    "Nx2 list indirect off: the device dispatches at most {}x{}x{} groups",
                    max_dim[0], max_dim[1], max_dim[2]);
    }
    return fits;''',
    '''    const bool narrow = max_dim[0] >= 0xffffU && max_dim[1] >= 0xffffU && max_dim[2] >= 0xffffU;
    if (!fits && narrow && VideoCommon::Nx2LiveSwitch("EDEN_NX2_LIST_INDIRECT_ANY_LIMIT") != "0") {
        g_nx2_narrow_grid_width.store(true, std::memory_order_relaxed);
        if (static std::atomic<bool> logged_any{}; !logged_any.exchange(true)) {
            LOG_INFO(Render_Vulkan,
                     "Nx2 list indirect on with 16-bit grid widths (the device dispatches at most "
                     "{}x{}x{} groups; EDEN_NX2_LIST_INDIRECT_ANY_LIMIT=0 turns it off)",
                     max_dim[0], max_dim[1], max_dim[2]);
        }
        return true;
    }
    if (static std::atomic<bool> logged{}; !fits && !logged.exchange(true)) {
        LOG_WARNING(Render_Vulkan,
                    "Nx2 list indirect off: the device dispatches at most {}x{}x{} groups",
                    max_dim[0], max_dim[1], max_dim[2]);
    }
    return fits;''')])
print("apply-nx2-list-indirect-any-limit: list indirect on 65535-wide devices (16-bit widths), default on")
