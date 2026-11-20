#!/usr/bin/env python3
"""Android: a failed surface re-creation drops the frame instead of aborting Eden.

Build 200 on the Odin (2026-11-19 18:54): Travis switched from Ocarina to another app; the present
thread found the surface lost, CreateSurface failed ("Failed to initialize Android surface",
VK_ERROR_INITIALIZATION_FAILED: the window was already gone), CopyToSwapchain rethrew everything but
the surface-lost family, and the uncaught exception aborted Eden in PresentManager::PresentThread.
On Android that error now counts as "surface not ready": the frame is dropped and the next one tries
again, as for VK_ERROR_SURFACE_LOST_KHR.

Usage: apply-nx2-present-surface-retry.py <upstream source root>
"""
import pathlib
import sys

path = pathlib.Path(sys.argv[1]) / "src/video_core/renderer_vulkan/vk_present_manager.cpp"
text = path.read_text()
if "NX2: the window was gone" in text:
    print("vk_present_manager.cpp: already patched")
    sys.exit(0)
old = """            const VkResult result = except.GetResult();
            if (result != VK_ERROR_SURFACE_LOST_KHR && result != VK_INCOMPLETE &&
                result != VK_ERROR_OUT_OF_DATE_KHR && result != VK_ERROR_NATIVE_WINDOW_IN_USE_KHR) {
                throw;
            }
"""
new = """            const VkResult result = except.GetResult();
#ifdef __ANDROID__
            // NX2: the window was gone when the surface was re-created (app switched away):
            // drop this frame and try again with the next one instead of aborting Eden.
            if (requires_recreation && result == VK_ERROR_INITIALIZATION_FAILED) {
                return;
            }
#endif
            if (result != VK_ERROR_SURFACE_LOST_KHR && result != VK_INCOMPLETE &&
                result != VK_ERROR_OUT_OF_DATE_KHR && result != VK_ERROR_NATIVE_WINDOW_IN_USE_KHR) {
                throw;
            }
"""
if text.count(old) != 1:
    sys.exit(f"vk_present_manager.cpp: CopyToSwapchain catch not found ({text.count(old)} matches)")
path.write_text(text.replace(old, new))
print("vk_present_manager.cpp: surface re-creation failures drop the frame")
