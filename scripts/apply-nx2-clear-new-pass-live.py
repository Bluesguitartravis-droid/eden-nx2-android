#!/usr/bin/env python3
"""EDEN_NX2_CLEAR_NEW_PASS re-read every 10 s (with the NX2_CLEARS report) instead of once at app
start, so moving depth/stencil clears into a pass of their own can be A/B'd in one session. On a
tiler each extra pass costs a tile load/store; Ocarina at the drawbridge moves ~200 a second.
Run after the script that adds Nx2ClearNewPass (apply-nx2-clear-new-pass.py)."""
from pathlib import Path
import sys

path = Path(sys.argv[1]).resolve() / "src/video_core/renderer_vulkan/vk_rasterizer.cpp"
text = path.read_text()
old = """bool Nx2ClearNewPass() {
    static const bool enabled = [] {
        const char* const value = std::getenv("EDEN_NX2_CLEAR_NEW_PASS");
        return value == nullptr || value[0] != '0'; // On unless EDEN_NX2_CLEAR_NEW_PASS=0
    }();
    return enabled;
}"""
new = """std::atomic<bool> nx2_clear_new_pass{true}; // scripts/apply-nx2-clear-new-pass-live.py
bool Nx2ClearNewPass() {
    return nx2_clear_new_pass.load(std::memory_order_relaxed);
}"""
if text.count(old) != 1:
    sys.exit("clear-new-pass-live: Nx2ClearNewPass not found once")
text = text.replace(old, new)
old2 = """    last = now;
    LOG_INFO(Render_Vulkan, "NX2_CLEARS 10s:"""
new2 = """    last = now;
    nx2_clear_new_pass.store(VideoCommon::Nx2LiveSwitch("EDEN_NX2_CLEAR_NEW_PASS") != "0",
                             std::memory_order_relaxed);
    LOG_INFO(Render_Vulkan, "NX2_CLEARS 10s:"""
if text.count(old2) != 1:
    sys.exit("clear-new-pass-live: report not found once")
text = text.replace(old2, new2)
if '#include "video_core/nx2_live_switch.h"' not in text:
    first = text.index("#include ")
    text = text[:first] + '#include "video_core/nx2_live_switch.h"\n' + text[first:]
path.write_text(text)
print("clear-new-pass-live: EDEN_NX2_CLEAR_NEW_PASS is read every 10 s")
