#!/usr/bin/env python3
"""Ask for cooperative matrix shapes only when the driver lists VK_KHR_cooperative_matrix.

Upstream checks only that the function pointer is non-null. Android's Vulkan loader, and
Turnip builds loaded through adrenotools, hand out a non-null entry point even when the
driver lacks the extension, and calling it jumps to address 0: every game crashed while
creating the Vulkan device (build 131/134 with Turnip Gen8 V34).
"""
from pathlib import Path
import sys

f = Path(sys.argv[1]) / "src/video_core/vulkan_common/vulkan_device.cpp"
t = f.read_text()
old = "        const auto shapes = physical.GetCooperativeMatrixPropertiesKHR();\n"
new = ("        const auto shapes =\n"
       "            supported_extensions.contains(VK_KHR_COOPERATIVE_MATRIX_EXTENSION_NAME)\n"
       "                ? physical.GetCooperativeMatrixPropertiesKHR()\n"
       "                : std::vector<VkCooperativeMatrixPropertiesKHR>{};\n")
if new in t:
    print("coopmat guard already present"); sys.exit(0)
n = t.count(old)
if n == 0:
    print("coopmat guard: upstream no longer queries shapes here; nothing to do"); sys.exit(0)
f.write_text(t.replace(old, new))
print(f"coopmat shapes queried only with the extension ({n} site(s))")
