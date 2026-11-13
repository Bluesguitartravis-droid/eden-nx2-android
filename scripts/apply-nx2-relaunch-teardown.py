#!/usr/bin/env python3
"""Starting a game after another one no longer crashes in the old game's GPU teardown.

When a game's stop did not finish (no NX2_SUMMARY in its log), the next start replaces the
Vulkan driver library (InitializeGpuDriver) and the render window before System::Load, and
Load's CreateGPU destroys the old GPU afterwards: its vkFreeMemory calls went through a closed
library (SIGSEGV in VmaAllocator_T::FreeDedicatedMemory from ~BufferCache, seen after Mario Kart
World twice). The previous library and window are now kept loaded until the new Load is done.
Usage: python3 scripts/apply-nx2-relaunch-teardown.py <upstream-root>
"""
from pathlib import Path
import sys

jni = Path(sys.argv[1]) / "src/android/app/src/main/jni"
MARK = "NX2 relaunch teardown"


def patch(name, old, new):
    path = jni / name
    text = path.read_text()
    if MARK in text:
        print(f"{name}: already patched")
        return
    if text.count(old) != 1:
        raise SystemExit(f"{name}: anchor found {text.count(old)} times: {old[:60]!r}")
    path.write_text(text.replace(old, new, 1))
    print(f"{name}: patched")


header = jni / "native.h"
if "#include <vector>\n" not in header.read_text():
    header.write_text(header.read_text().replace("#include <optional>\n", "#include <optional>\n#include <vector>\n", 1))

patch("native.h",
      "    std::shared_ptr<Common::DynamicLibrary> m_vulkan_library;\n",
      "    std::shared_ptr<Common::DynamicLibrary> m_vulkan_library;\n"
      "    // " + MARK + ": earlier driver libraries stay loaded, as a GPU that was not\n"
      "    // destroyed at its game's stop is destroyed by the next game's Load.\n"
      "    std::vector<std::shared_ptr<Common::DynamicLibrary>> m_nx2_retired_vulkan_libraries;\n")

patch("native.cpp",
      "    m_vulkan_library = std::make_shared<Common::DynamicLibrary>(handle);\n#endif\n",
      "    if (m_vulkan_library) {\n"
      "        // " + MARK + "\n"
      "        m_nx2_retired_vulkan_libraries.push_back(m_vulkan_library);\n"
      "    }\n"
      "    m_vulkan_library = std::make_shared<Common::DynamicLibrary>(handle);\n#endif\n")

patch_text = (jni / "native.cpp").read_text()
old = ("    // Create the render window.\n"
       "    m_window = std::make_unique<EmuWindow_Android>(m_native_window, m_vulkan_library);\n")
if "nx2_previous_window" not in patch_text:
    if patch_text.count(old) != 1:
        raise SystemExit("native.cpp: render window creation not found")
    patch_text = patch_text.replace(old,
        "    // NX2: the previous window (and the driver library it holds) lives until this function\n"
        "    // returns, after Load has destroyed a GPU that was still there.\n"
        "    const auto nx2_previous_window = std::move(m_window);\n" + old, 1)
    (jni / "native.cpp").write_text(patch_text)
    print("native.cpp: previous window kept through Load")
