#!/usr/bin/env python3
"""EDEN_NX2_DISPLAY_DEPTH=<n> (measurement aid, live; needs apply-nx2-skip-shaders.py first): how
many presents may be queued on the display before the game starts its next frame, while the host
display starts the frames of a game whose frame rate is unlocked (VideoCore::HostVsync).

The display starts the next frame once the present before the last is on screen (depth 1, as
before). Ocarina's main thread waits ~17 ms a frame after its present call, and its next frame
cannot start until frame N-1 went through the GPU split, the host GPU and the display.
  0  the display never starts frames: the vsync timer does (60 Hz), as for a locked game
  2+ start the next frame once the present n back is on screen (deeper queue)"""
from pathlib import Path
import sys

path = Path(sys.argv[1]).resolve() / "src/video_core/renderer_vulkan/vk_present_manager.cpp"
text = path.read_text()


def replace(old, new):
    global text
    if text.count(old) != 1:
        sys.exit(f"display-depth: expected 1 match, found {text.count(old)}:\n{old}")
    text = text.replace(old, new)


first_include = text.index("#include")
text = text[:first_include] + '#include "video_core/nx2_live_switch.h"\n' + text[first_include:]
replace("""void PresentManager::WaitForDisplay() {
    if (last_present_id < 2 || !swapchain.WaitsForDisplay() ||
        display_silent.load(std::memory_order_acquire)) {
        return;
    }
    // Long enough for any refresh rate, short enough that a window that shows nothing does not
    // stall presentation.
    constexpr std::chrono::milliseconds Timeout{100};
    const u64 target = last_present_id - 1;
""", """void PresentManager::WaitForDisplay() {
    // NX2 display depth (scripts/apply-nx2-display-depth.py)
    const std::string nx2_depth_text = VideoCommon::Nx2LiveSwitch("EDEN_NX2_DISPLAY_DEPTH");
    const u64 nx2_depth =
        nx2_depth_text.empty() ? 1 : std::strtoull(nx2_depth_text.c_str(), nullptr, 10);
    if (nx2_depth == 0) {
        return;
    }
    if (last_present_id < nx2_depth + 1 || !swapchain.WaitsForDisplay() ||
        display_silent.load(std::memory_order_acquire)) {
        return;
    }
    // Long enough for any refresh rate, short enough that a window that shows nothing does not
    // stall presentation.
    constexpr std::chrono::milliseconds Timeout{100};
    const u64 target = last_present_id - nx2_depth;
""")
if "#include <cstdlib>" not in text:
    text = "#include <cstdlib>\n" + text
path.write_text(text)
print("display-depth: EDEN_NX2_DISPLAY_DEPTH added")
