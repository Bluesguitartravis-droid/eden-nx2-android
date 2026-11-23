#!/usr/bin/env python3
"""EDEN_NX2_DISPATCH_BARRIER=0 (measurement aid, live; needs apply-nx2-skip-shaders.py first):
record no barrier in front of compute dispatches. Eden puts a full barrier (graphics, compute and
transfer writes -> compute and indirect reads) before every dispatch, so ~150 small dispatches a
frame in Ocarina each wait for the GPU to drain. NVIDIA hardware runs consecutive grids together
unless the game asks for a wait for idle (which Eden turns into its own barrier), so most of these
may be unneeded. Unsafe as a setting (emulator copies before a dispatch lose their ordering): this
only prices them."""
from pathlib import Path
import sys

path = Path(sys.argv[1]).resolve() / "src/video_core/renderer_vulkan/vk_rasterizer.cpp"
text = path.read_text()


def replace(old, new):
    global text
    if text.count(old) != 1:
        sys.exit(f"dispatch-barrier: expected 1 match, found {text.count(old)}:\n{old}")
    text = text.replace(old, new)


replace("""/// Earlier writes must be visible to a dispatch, including its indirect arguments.
void RecordDispatchBarrier(vk::CommandBuffer cmdbuf) {
""", """/// NX2: EDEN_NX2_DISPATCH_BARRIER=0 (scripts/apply-nx2-dispatch-barrier.py)
std::atomic<bool> nx2_no_dispatch_barrier{false};

/// Earlier writes must be visible to a dispatch, including its indirect arguments.
void RecordDispatchBarrier(vk::CommandBuffer cmdbuf) {
    if (nx2_no_dispatch_barrier.load(std::memory_order_relaxed)) {
        return;
    }
""")
replace("""void RasterizerVulkan::DispatchCompute() {
""", """void RasterizerVulkan::DispatchCompute() {
    nx2_no_dispatch_barrier.store(VideoCommon::Nx2LiveSwitch("EDEN_NX2_DISPATCH_BARRIER") == "0",
                                  std::memory_order_relaxed);
""")
if "#include <atomic>" not in text:
    text = "#include <atomic>\n" + text
path.write_text(text)
print("dispatch-barrier: EDEN_NX2_DISPATCH_BARRIER added")
