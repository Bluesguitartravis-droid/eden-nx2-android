#!/usr/bin/env python3
"""EDEN_NX2_NCE_COLLECT_WRITES=1 is read at each game boot from the switches file (it was read once
per process from the environment).

Build 249: Ocarina on NCE renders right with open pages off, but at 12.8 fps (14.6 on Dynarmic):
5,500 write faults a second, 2,700 of them waiting for the GPU thread (111 ms/s). Collected
writes execute the faulting store on the emulated memory and hand the GPU its exact bytes, as
Dynarmic's writes are, so those faults stop waiting for the GPU."""
from pathlib import Path
import sys

src = Path(sys.argv[1]).resolve() / "src"


def replace(path, old, new):
    text = path.read_text()
    if text.count(old) != 1:
        sys.exit(f"nce-collect-live: {path.name}: expected 1 match, found {text.count(old)}")
    path.write_text(text.replace(old, new))


replace(src / "common/nx2_nce_stats.h", "inline std::atomic<bool> open_pages{true};\n",
        "inline std::atomic<bool> open_pages{true};\n"
        "// EDEN_NX2_NCE_COLLECT_WRITES (default off), set at each game boot.\n"
        "inline std::atomic<bool> collect_writes{false};\n")
replace(src / "core/memory.cpp", """    static const bool enabled = [] {
        // Off unless asked for: build 145 measured Donkey Kong Bananza slower with it (its writes
        // to these pages are mostly bursts: 33,000 emulated stores/s and still 1,400 waits/s).
        const char* value = std::getenv("EDEN_NX2_NCE_COLLECT_WRITES");
        return value != nullptr && value[0] == '1';
    }();""", """    // Off unless asked for: build 145 measured Donkey Kong Bananza slower with it (its writes
    // to these pages are mostly bursts: 33,000 emulated stores/s and still 1,400 waits/s).
    const bool enabled = Common::Nx2NceStats::collect_writes.load(std::memory_order_relaxed);""")
replace(src / "core/device_memory.cpp", """    LOG_INFO(Core, "NX2 NCE open pages: {}", Common::Nx2NceStats::open_pages.load());
""", """    Common::Nx2NceStats::collect_writes.store(
        VideoCommon::Nx2LiveSwitch("EDEN_NX2_NCE_COLLECT_WRITES") == "1");
    LOG_INFO(Core, "NX2 NCE open pages: {}, collected writes: {}",
             Common::Nx2NceStats::open_pages.load(), Common::Nx2NceStats::collect_writes.load());
""")
print("nce-collect-live: EDEN_NX2_NCE_COLLECT_WRITES read at each game boot")
