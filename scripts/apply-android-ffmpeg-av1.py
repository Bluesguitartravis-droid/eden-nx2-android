#!/usr/bin/env python3
"""Use the FFmpeg+dav1d built by build-android-ffmpeg-av1.sh on Android (AV1 movies).

usage: apply-android-ffmpeg-av1.py <upstream> <absolute ffmpeg dir>
"""
from pathlib import Path
import sys

root, ffdir = Path(sys.argv[1]), sys.argv[2]
cm = root / "externals/ffmpeg/CMakeLists.txt"
t = cm.read_text()
old = """    if ((MSVC OR LINUX) AND CPMUTIL_AMD64)
        AddJsonPackage(ffmpeg-ci-av1)"""
new = """    if (ANDROID AND NX2_ANDROID_FFMPEG_DIR AND EXISTS "${NX2_ANDROID_FFMPEG_DIR}/CMakeLists.txt")
        # Eden NX2 Android: FFmpeg with libdav1d for Switch 2 AV1 movies.
        add_subdirectory(${NX2_ANDROID_FFMPEG_DIR} ${CMAKE_CURRENT_BINARY_DIR}/nx2-ffmpeg)
        set(FFmpeg_SOURCE_DIR ${NX2_ANDROID_FFMPEG_DIR})
        message(STATUS "Using Eden NX2 Android FFmpeg with AV1 from ${NX2_ANDROID_FFMPEG_DIR}")
    elseif ((MSVC OR LINUX) AND CPMUTIL_AMD64)
        AddJsonPackage(ffmpeg-ci-av1)"""
if "NX2_ANDROID_FFMPEG_DIR" not in t:
    if old not in t:
        raise SystemExit("externals/ffmpeg/CMakeLists.txt shape changed")
    cm.write_text(t.replace(old, new, 1))

gradle = root / "src/android/app/build.gradle.kts"
g = gradle.read_text()
anchor = '                        "-DENABLE_WEB_SERVICE=1", // Enable web service\n'
if "NX2_ANDROID_FFMPEG_DIR" not in g:
    if anchor not in g:
        raise SystemExit("build.gradle.kts: CMake argument block missing")
    g = g.replace(anchor, anchor + f'                        "-DNX2_ANDROID_FFMPEG_DIR={ffdir}",\n', 1)
    gradle.write_text(g)
print(f"Android FFmpeg with AV1: {ffdir}")
