#!/usr/bin/env python3
"""EDEN_NX2_DLSS_REPLACEMENT read at each game boot instead of once at app start.

Ocarina's upscaler (the game's DLSS network on the Switch 2's tensor cores) is replaced: fsr = FSR
3.1 (~2.5 ms/frame on the Odin), standin = Eden's temporal stand-in, unset = the game's own pass
(Native in handheld). ResolveOcarinaUpscaler runs once per rasterizer (game boot) and
WantsTemporalUpscaleDepth per frame; both now ask Nx2LiveSwitch (file re-read at most every 2 s), so
the three can be compared with exit_game / launch_game."""
from pathlib import Path
import sys

vk = Path(sys.argv[1]).resolve() / "src/video_core/renderer_vulkan"


def patch(name, old, new):
    path = vk / name
    text = path.read_text()
    if text.count(old) != 1:
        sys.exit(f"dlss-replacement-live: {name}: expected 1 match, found {text.count(old)}:\n{old}")
    text = text.replace(old, new)
    if '#include "video_core/nx2_live_switch.h"' not in text:
        first = text.index("#include ")
        text = text[:first] + '#include "video_core/nx2_live_switch.h"\n' + text[first:]
    path.write_text(text)


patch("vk_ocarina_camera_motion.cpp",
      """    const char* value = std::getenv("EDEN_NX2_DLSS_REPLACEMENT");
    bool dlss_available = false;""",
      """    // NX2: read at each game boot (scripts/apply-nx2-dlss-replacement-live.py)
    const std::string replacement = VideoCommon::Nx2LiveSwitch("EDEN_NX2_DLSS_REPLACEMENT");
    const char* value = replacement.empty() ? nullptr : replacement.c_str();
    bool dlss_available = false;""")
patch("vk_texture_cache.cpp",
      """    static const bool force_standin = [] {
        const char* const value = std::getenv("EDEN_NX2_DLSS_REPLACEMENT");
        return value && std::string_view{value} == "standin";
    }();""",
      """    // NX2: read live (scripts/apply-nx2-dlss-replacement-live.py)
    const bool force_standin = VideoCommon::Nx2LiveSwitch("EDEN_NX2_DLSS_REPLACEMENT") == "standin";""")
print("dlss-replacement-live: EDEN_NX2_DLSS_REPLACEMENT is read at each game boot")
