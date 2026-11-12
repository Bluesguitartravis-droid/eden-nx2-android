#!/usr/bin/env python3
"""Switch 2 movies: frame-threaded CPU decoding (Ocarina of Time's choppy title video).

FFmpeg::DecoderContext sets AV_CODEC_FLAG_LOW_DELAY on Android and turns frame threading off,
for NVDEC's one-frame-in, one-frame-out use. The nx2 video service (vdecsvc) queues its input and
copes with frames that come out later, but it builds on the same context, so libdav1d ran with
max_frame_delay 1: one frame at a time, its workers mostly idle. Build 157's trace of Ocarina's
1080p60 AV1 title movie: decode calls p90 35 ms, p99 108 ms, the decode thread 100% busy, about
33 video frames a second written and 989 of 1200 frames dropped late.

For CPU decoders (not MediaCodec, not a GPU hwaccel) this clears LOW_DELAY and enables frame
threading, so libdav1d (max_frame_delay auto) and FFmpeg's VP9 decode several frames at once.
EDEN_NX2_VIDEO_FRAME_THREADS=0 restores the old setting.
First built in build 161.
Usage: python3 scripts/apply-nx2-video-frame-threads.py <upstream-root>
"""
from pathlib import Path
import sys

path = Path(sys.argv[1]) / "src/video_core/host1x/nx2_video_decoder.cpp"
text = path.read_text()
MARK = "NX2 frame-threaded movie decoding"
if MARK in text:
    print("already patched")
    raise SystemExit(0)
anchor = ("    const bool gpu = Settings::values.nvdec_emulation.GetValue() == "
          "Settings::NvdecEmulation::Gpu;\n")
if text.count(anchor) != 1:
    raise SystemExit("nx2_video_decoder.cpp: gpu setting line not found")
text = text.replace(anchor, anchor + r'''    // NX2 frame-threaded movie decoding: the service queues input and takes frames later, so a CPU
    // decoder may work on several frames at once (DecoderContext set low delay for NVDEC).
    static const bool nx2_frame_threads{[] {
        const char* const value{std::getenv("EDEN_NX2_VIDEO_FRAME_THREADS")};
        return value == nullptr || value[0] != '0';
    }()};
    if (nx2_frame_threads && !gpu && decoder_name.find("mediacodec") == std::string_view::npos) {
        context->flags &= ~AV_CODEC_FLAG_LOW_DELAY;
        context->thread_type |= FF_THREAD_FRAME;
        context->thread_count = 0;
        LOG_INFO(HW_GPU, "nx2 video: \"{}\" with {}: frame threading on, low delay off", mime,
                 decoder_name);
    }
''', 1)
if "#include <cstdlib>\n" not in text:
    text = text.replace("#include <cstring>\n", "#include <cstdlib>\n#include <cstring>\n", 1)
path.write_text(text)
print("frame-threaded movie decoding installed")
