#!/usr/bin/env python3
"""EDEN_NX2_CLEAR_NEW_PASS=1: a full depth/stencil clear that arrives inside an active render pass ends
the pass and becomes the next pass's loadOp=CLEAR, instead of vkCmdClearAttachments.

Turnip disables LRZ (its low-resolution depth buffer, early Z rejection) for the rest of a render
pass when vkCmdClearAttachments clears depth: TU_DEBUG=perf on the Odin logged "Disabling LRZ because
'CmdClearAttachments'" 766 times in the first ~1,200 lines in Hyrule Field, the most common reason.
A pass break costs a store/load of the attachments; LRZ saves the shading of hidden fragments.
Always on: an NX2_CLEARS line every 10 s counts mid-pass depth clears and how many were turned into
a new pass."""
from pathlib import Path
import sys

f = Path(sys.argv[1]).resolve() / "src/video_core/renderer_vulkan/vk_rasterizer.cpp"
t = f.read_text()
if "Nx2ClearNewPass" in t:
    print("vk_rasterizer.cpp: already patched")
    sys.exit(0)

def edit(old, new):
    global t
    if t.count(old) != 1:
        sys.exit(f"vk_rasterizer.cpp: expected one match for {old[:70]!r}, found {t.count(old)}")
    t = t.replace(old, new, 1)

edit("void RasterizerVulkan::Clear(u32 layer_count) {", """namespace {
// scripts/apply-nx2-clear-new-pass.py
bool Nx2ClearNewPass() {
    static const bool enabled = [] {
        const char* const value = std::getenv("EDEN_NX2_CLEAR_NEW_PASS");
        return value != nullptr && value[0] == '1';
    }();
    return enabled;
}
std::atomic<u64> nx2_mid_pass_ds_clears{};
std::atomic<u64> nx2_ds_clears_new_pass{};
void Nx2ReportClears() {
    static std::chrono::steady_clock::time_point last{std::chrono::steady_clock::now()};
    const auto now = std::chrono::steady_clock::now();
    if (now - last < std::chrono::seconds{10}) {
        return;
    }
    last = now;
    LOG_INFO(Render_Vulkan, "NX2_CLEARS 10s: depth/stencil clears inside a pass {} (vkCmdClearAttachments, "
             "Turnip drops LRZ), moved to a new pass {} (EDEN_NX2_CLEAR_NEW_PASS={})",
             nx2_mid_pass_ds_clears.exchange(0), nx2_ds_clears_new_pass.exchange(0),
             Nx2ClearNewPass() ? 1 : 0);
}
} // Anonymous namespace

void RasterizerVulkan::Clear(u32 layer_count) {""")

edit("""    const bool can_defer_clear = ENABLE_DEFERRED_CLEAR && (!regs.clear_control.use_scissor || clear_covers_render_area) &&""",
     """    Nx2ReportClears();
    if (Nx2ClearNewPass() && ds_used && ds_deferrable && scheduler.IsRenderPassActive() &&
        (!regs.clear_control.use_scissor || clear_covers_render_area) &&
        clear_layers.covers_all_layers && (!use_color || color_full_channels)) {
        // NX2 clear new pass: the clear becomes the next pass's loadOp (Turnip keeps LRZ).
        scheduler.RequestOutsideRenderPassOperationContext();
        nx2_ds_clears_new_pass.fetch_add(1, std::memory_order_relaxed);
    }
    const bool can_defer_clear = ENABLE_DEFERRED_CLEAR && (!regs.clear_control.use_scissor || clear_covers_render_area) &&""")

edit("""        scheduler.Record([clear_depth = regs.clear_depth, clear_stencil = regs.clear_stencil,
                          clear_rect, aspect_flags](vk::CommandBuffer cmdbuf) {
            VkClearAttachment attachment;""",
     """        nx2_mid_pass_ds_clears.fetch_add(1, std::memory_order_relaxed);
        scheduler.Record([clear_depth = regs.clear_depth, clear_stencil = regs.clear_stencil,
                          clear_rect, aspect_flags](vk::CommandBuffer cmdbuf) {
            VkClearAttachment attachment;""")
f.write_text(t)
print("vk_rasterizer.cpp: NX2_CLEARS counted; EDEN_NX2_CLEAR_NEW_PASS=1 moves in-pass depth clears to a new pass")
