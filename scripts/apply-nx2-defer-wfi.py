#!/usr/bin/env python3
"""Guest wait-for-idle inside a render pass no longer ends the pass (Adreno/Turnip).

Build 210, Ocarina in Hyrule Field at night: 3,587 render passes a second (~220 a frame at
16 fps); 870/s ended at RasterizerVulkan::WaitForIdle, the guest's WFI between draws. On a tiler
every end and restart stores and reloads the attachments.

Inside a render pass whose draws wrote no storage (NoteRenderPassStores), a WFI orders nothing
the pass itself needs: draws on the same attachments are already in order, a draw that samples
an attachment breaks the pass through the deferred fragment barrier (as on MoltenVK), and the
WFI's full barrier is recorded when the pass ends, after the attachment barriers, so later
copies and dispatches still wait for every draw. EDEN_NX2_DEFER_WFI=0 restores the old path.

Usage: apply-nx2-defer-wfi.py <upstream source root>
"""
import pathlib
import sys

root = pathlib.Path(sys.argv[1]) / "src/video_core/renderer_vulkan"


def patch(name, edits):
    path = root / name
    text = path.read_text()
    if "NX2 deferred WFI" in text:
        print(f"{name}: already patched")
        return
    for old, new in edits:
        if text.count(old) != 1:
            sys.exit(f"{name}: expected one match for {old[:70]!r}, found {text.count(old)}")
        text = text.replace(old, new)
    path.write_text(text)
    print(f"{name}: patched")


patch("vk_scheduler.h", [
    ("""    void DeferFragmentBarrier() {
        fragment_barrier.Defer(state.renderpass != VK_NULL_HANDLE);
    }""", """    void DeferFragmentBarrier() {
        fragment_barrier.Defer(state.renderpass != VK_NULL_HANDLE);
    }

    /// NX2 deferred WFI: a wait-for-idle inside the render pass, recorded when the pass ends.
    void DeferWaitForIdle(VkPipelineStageFlags flags) {
        deferred_wfi_stages |= flags;
        fragment_barrier.Defer(state.renderpass != VK_NULL_HANDLE);
    }"""),
    ("""    bool render_pass_has_stores{};""", """    bool render_pass_has_stores{};
    VkPipelineStageFlags deferred_wfi_stages{};"""),
])

patch("vk_scheduler.cpp", [
    ("""        ++recording_epoch;
        fragment_barrier.Reset();
        render_pass_has_stores = false;""", """        ++recording_epoch;
        fragment_barrier.Reset();
        render_pass_has_stores = false;
        // NX2 deferred WFI: recorded after the pass and its attachment barriers.
        const VkPipelineStageFlags wfi_stages = std::exchange(deferred_wfi_stages, 0);"""),
    ("""        state.renderpass = VkRenderPass{};
        num_renderpass_images = 0;
    }
""", """        if (wfi_stages != 0) {
            Record([wfi_stages](vk::CommandBuffer cmdbuf) {
                static constexpr VkMemoryBarrier WFI_BARRIER{
                    .sType = VK_STRUCTURE_TYPE_MEMORY_BARRIER,
                    .pNext = nullptr,
                    .srcAccessMask = VK_ACCESS_MEMORY_WRITE_BIT,
                    .dstAccessMask = VK_ACCESS_MEMORY_READ_BIT | VK_ACCESS_MEMORY_WRITE_BIT,
                };
                cmdbuf.PipelineBarrier(wfi_stages, VK_PIPELINE_STAGE_ALL_COMMANDS_BIT, 0,
                                       WFI_BARRIER);
            });
        }

        state.renderpass = VkRenderPass{};
        num_renderpass_images = 0;
    }
"""),
])

patch("vk_rasterizer.cpp", [
    ("""    if (device.IsMoltenVK() && scheduler.IsRenderPassActive()) {
        scheduler.DeferFragmentBarrier();
        fence_manager.SignalOrdering();
        return;
    }
    scheduler.RequestOutsideRenderPassOperationContext();
    scheduler.Record([flags](vk::CommandBuffer cmdbuf) {""", """    if (device.IsMoltenVK() && scheduler.IsRenderPassActive()) {
        scheduler.DeferFragmentBarrier();
        fence_manager.SignalOrdering();
        return;
    }
    // NX2 deferred WFI: draws that wrote no storage need nothing from a WFI inside their pass.
    static const bool defer_wfi = [] {
        const char* value = std::getenv("EDEN_NX2_DEFER_WFI");
        return value == nullptr || value[0] != '0';
    }();
    if (defer_wfi && scheduler.IsRenderPassActive() && !scheduler.RenderPassHasStores()) {
        scheduler.DeferWaitForIdle(flags);
        fence_manager.SignalOrdering();
        return;
    }
    scheduler.RequestOutsideRenderPassOperationContext();
    scheduler.Record([flags](vk::CommandBuffer cmdbuf) {"""),
])
