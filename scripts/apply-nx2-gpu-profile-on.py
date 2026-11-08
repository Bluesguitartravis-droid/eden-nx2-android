#!/usr/bin/env python3
"""Test builds: turn on Eglantine/Lichii's GPU-thread profiler on Android.

nx2_profile.h times where the GPU thread spends its time (draw, pipeline lookup,
waits on the Vulkan GPU, ...) and logs each zone's share every 10 s, but only
when EDEN_NX2_GPU_PROFILE is set, which an Android app cannot do.
"""
from pathlib import Path
import sys

f = Path(sys.argv[1]) / "src/video_core/nx2_profile.h"
t = f.read_text()
old = 'inline const bool g_enabled = std::getenv("EDEN_NX2_GPU_PROFILE") != nullptr;'
new = '''#if defined(__ANDROID__)
inline const bool g_enabled = true; // Eden NX2 Android test builds: always profile
#else
inline const bool g_enabled = std::getenv("EDEN_NX2_GPU_PROFILE") != nullptr;
#endif'''
if new in t or "Eden NX2 Android test builds: always profile" in t:
    print("GPU profile already on"); sys.exit(0)
# Newer upstream: a plain flag that ReadSwitch() re-reads after nx2_switches.txt loads.
old2 = 'inline bool g_enabled = std::getenv("EDEN_NX2_GPU_PROFILE") != nullptr;'
read2 = '    g_enabled = std::getenv("EDEN_NX2_GPU_PROFILE") != nullptr;\n'
if old2 in t and read2 in t:
    t = t.replace(old2, 'inline bool g_enabled = true; // Eden NX2 Android test builds: always profile', 1)
    t = t.replace(read2, '    g_enabled = true; // Eden NX2 Android test builds: always profile\n', 1)
    f.write_text(t)
    print("NX2 GPU-thread profiler enabled (ReadSwitch shape)"); sys.exit(0)
if old not in t:
    raise SystemExit("nx2_profile.h: g_enabled shape changed")
f.write_text(t.replace(old, new, 1))
print("NX2 GPU-thread profiler enabled on Android")
