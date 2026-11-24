#!/usr/bin/env python3
"""EDEN_NX2_NCE_OPEN_ALL=1 (read at each game boot): open every GPU-cached page at an NCE write
fault, not only those that hold GPU-written bytes.

253 (EDEN_NX2_NCE_OPEN_PAGES=2 + EDEN_NX2_NCE_OPEN_WHOLE=1) renders Ocarina correctly on NCE at
14.9 fps, but its guest cores still take ~5,700 write faults a second (~200-240 ms/s in the
handler) on cached pages without GPU writes: each one invalidates the page, the GPU caches it
again for its next use, and the next CPU write faults again. Opened instead, a page takes no more
faults until the GPU thread's next gather hands it over. Only pages that hold GPU writes are
downloaded first."""
from pathlib import Path
import sys

src = Path(sys.argv[1]).resolve() / "src"


def replace(path, old, new):
    text = path.read_text()
    if text.count(old) != 1:
        sys.exit(f"nce-open-all: {path.name}: expected 1 match, found {text.count(old)}:\n{old}")
    path.write_text(text.replace(old, new))


replace(src / "common/nx2_nce_stats.h", "inline std::atomic<bool> open_pages{true};\n",
        "inline std::atomic<bool> open_pages{true};\n"
        "// EDEN_NX2_NCE_OPEN_ALL: open every cached page at a write fault.\n"
        "inline std::atomic<bool> open_all{false};\n")

replace(src / "core/device_memory.cpp", """    Common::Nx2NceStats::open_whole.store(
        VideoCommon::Nx2LiveSwitch("EDEN_NX2_NCE_OPEN_WHOLE") == "1");
""", """    Common::Nx2NceStats::open_whole.store(
        VideoCommon::Nx2LiveSwitch("EDEN_NX2_NCE_OPEN_WHOLE") == "1");
    Common::Nx2NceStats::open_all.store(
        VideoCommon::Nx2LiveSwitch("EDEN_NX2_NCE_OPEN_ALL") == "1");
""")

memory = src / "core/memory.cpp"
replace(memory, """        if (!gpu_device_memory->IsPhysicalPointer(ptr) || !Nx2HostPageHoldsGpuWrites(ptr)) {
            return false;
        }
        // NX2: =2 brings the GPU's bytes into the page first (scripts/apply-nx2-nce-open-download.py)
        if (Common::Nx2NceStats::open_download.load(std::memory_order_relaxed)) {""", """        if (!gpu_device_memory->IsPhysicalPointer(ptr)) {
            return false;
        }
        // NX2: EDEN_NX2_NCE_OPEN_ALL opens pages without GPU writes too
        // (scripts/apply-nx2-nce-open-all.py)
        const bool nx2_open_all = Common::Nx2NceStats::open_all.load(std::memory_order_relaxed);
        const bool nx2_gpu_written = Nx2HostPageHoldsGpuWrites(ptr);
        if (!nx2_gpu_written && !nx2_open_all) {
            return false;
        }
        // NX2: =2 brings the GPU's bytes into the page first (scripts/apply-nx2-nce-open-download.py)
        if (nx2_gpu_written && Common::Nx2NceStats::open_download.load(std::memory_order_relaxed)) {""")
replace(memory, """            if (nx2_open_pages.size() >= Nx2MaxOpenPages || !Nx2HostPageHoldsGpuWrites(ptr)) {""",
        """            if (nx2_open_pages.size() >= Nx2MaxOpenPages ||
                (!nx2_open_all && !Nx2HostPageHoldsGpuWrites(ptr))) {""")
print("nce-open-all: EDEN_NX2_NCE_OPEN_ALL added")
