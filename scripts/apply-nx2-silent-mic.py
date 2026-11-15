#!/usr/bin/env python3
"""Android: give Switch 2 microphone streams silence instead of opening a host capture stream.

Since upstream's Switch 2 microphone capture (#497), Ocarina of Time opens an input stream as it
boots. On Android, SDL3 opening a recording device asks for RECORD_AUDIO and waits for the answer,
which only SDL's own activity delivers: Eden never gets it, so the game's main thread sat in that
call forever (black screen, every guest thread waiting; builds 131-137). The upstream fallback
for an input stream without a ready host is SilentInputStream; use it directly on Android.
"""
from pathlib import Path
import sys

f = Path(sys.argv[1]) / "src/core/hle/service/audio/nx2_sysmix.cpp"
t = f.read_text()
old = """    p.host = p.sink.AcquireSinkStream(p.system, channels, "sysmix-" + std::to_string(p.id),
                                      p.input_stream ? AudioCore::Sink::StreamType::In
                                                     : AudioCore::Sink::StreamType::Sysmix);
"""
new = """#ifdef __ANDROID__
    // NX2 Android: no host capture (SDL3's recording permission wait never returns here).
    p.host = p.input_stream
                 ? nullptr
                 : p.sink.AcquireSinkStream(p.system, channels, "sysmix-" + std::to_string(p.id),
                                            AudioCore::Sink::StreamType::Sysmix);
#else
    p.host = p.sink.AcquireSinkStream(p.system, channels, "sysmix-" + std::to_string(p.id),
                                      p.input_stream ? AudioCore::Sink::StreamType::In
                                                     : AudioCore::Sink::StreamType::Sysmix);
#endif
"""
if "NX2 Android: no host capture" in t:
    print("silent mic already applied"); sys.exit(0)
if old not in t:
    raise SystemExit("nx2_sysmix.cpp: AcquireSinkStream shape changed")
f.write_text(t.replace(old, new, 1))
print("Switch 2 microphone streams use silent input on Android")
