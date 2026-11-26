#!/usr/bin/env python3
"""Android build tuning (282): ThinLTO, -O3 and ARMv8.2 code for the native libraries.

The Android APK was built RelWithDebInfo (-O2), without link-time optimization and for baseline
ARMv8.0 (atomics through outline helper calls). The desktop forks that publish measured gains
(Lichii's own PGO builds, selevet-eglantine, Orchid) all build with LTO; upstream's ENABLE_LTO
option already keeps kernel.cpp out of it (fibers and TLS).

- ENABLE_LTO=ON: CMake's interprocedural optimization (clang: -flto=thin).
- LICHII_BUILD_PRESET=optimized: -march=armv8.2-a+lse+rcpc (every Snapdragon since the 845; the
  APK no longer starts on ARMv8.0-only chips).
- RelWithDebInfo's -O2 becomes -O3.
"""
from pathlib import Path
import sys

root = Path(sys.argv[1]).resolve()

props = root / "src/android/gradle.properties"
text = props.read_text()
if "LICHII_ANDROID_ARGS" in text:
    sys.exit("apply-android-lto: gradle.properties already sets LICHII_ANDROID_ARGS")
if not text.endswith("\n"):
    text += "\n"
text += "LICHII_ANDROID_ARGS=-DENABLE_LTO=ON -DLICHII_BUILD_PRESET=optimized\n"
props.write_text(text)

gradle = (root / "src/android/app/build.gradle.kts").read_text()
if 'project.findProperty("LICHII_ANDROID_ARGS")' not in gradle:
    sys.exit("apply-android-lto: build.gradle.kts no longer reads LICHII_ANDROID_ARGS")

cmake = root / "CMakeLists.txt"
text = cmake.read_text()
old = """# Configure C++ standard
# ===========================
"""
if text.count(old) != 1:
    sys.exit("apply-android-lto: C++ standard anchor not found once in CMakeLists.txt")
new = """if (ANDROID)
    # Eden NX2 Android: the NDK's RelWithDebInfo is -O2.
    foreach(nx2_flags CMAKE_C_FLAGS_RELWITHDEBINFO CMAKE_CXX_FLAGS_RELWITHDEBINFO)
        string(REPLACE "-O2" "-O3" ${nx2_flags} "${${nx2_flags}}")
    endforeach()
    message(STATUS "NX2 Android flags: LTO=${ENABLE_LTO} preset=${LICHII_BUILD_PRESET} "
                   "relwithdebinfo='${CMAKE_CXX_FLAGS_RELWITHDEBINFO}' cxx='${CMAKE_CXX_FLAGS}'")
endif()

""" + old
cmake.write_text(text.replace(old, new))
print("apply-android-lto: applied")
