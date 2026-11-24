#!/usr/bin/env python3
"""EDEN_NX2_NCE_OPEN_PAGES=2 (read at each game boot): download the GPU's bytes into the page
before opening it for writing.

Open pages (=1, the default) open a GPU-cached page that holds GPU-written bytes without
downloading them: CPU reads on the open page see stale bytes, and a store of a value guest memory
already holds is not seen. Ocarina on NCE renders a white world with it (248) and correctly
without it (249, 12.8 fps: 5,500 write faults/s, 2,700 of them waiting for the GPU thread).

With =2 guest memory holds the GPU's bytes when the copy is taken, so the gathered differences
are exactly the CPU's writes and reads see current data, while later writes to the page until the
next gather no longer fault. The download runs before nx2_open_mutex is taken: the GPU thread
holds that mutex while it gathers."""
from pathlib import Path
import sys

src = Path(sys.argv[1]).resolve() / "src"


def replace(path, old, new):
    text = path.read_text()
    if text.count(old) != 1:
        sys.exit(f"nce-open-download: {path.name}: expected 1 match, found {text.count(old)}")
    path.write_text(text.replace(old, new))


replace(src / "common/nx2_nce_stats.h", "inline std::atomic<bool> open_pages{true};\n",
        "inline std::atomic<bool> open_pages{true};\n"
        "// EDEN_NX2_NCE_OPEN_PAGES=2: download the GPU's bytes before opening a page.\n"
        "inline std::atomic<bool> open_download{false};\n"
        "inline std::atomic<std::uint64_t> open_downloads{};\n")

replace(src / "core/memory.cpp", """        if (!gpu_device_memory->IsPhysicalPointer(ptr) || !Nx2HostPageHoldsGpuWrites(ptr)) {
            return false;
        }
        // Held by the GPU thread while it applies the writes of the pages it closed.
        std::scoped_lock lock{nx2_open_mutex};""", """        if (!gpu_device_memory->IsPhysicalPointer(ptr) || !Nx2HostPageHoldsGpuWrites(ptr)) {
            return false;
        }
        // NX2: =2 brings the GPU's bytes into the page first (scripts/apply-nx2-nce-open-download.py)
        if (Common::Nx2NceStats::open_download.load(std::memory_order_relaxed)) {
            DownloadFromRasterizer(vaddr & ~LICHII_PAGEMASK, LICHII_PAGESIZE);
            Common::Nx2NceStats::open_downloads.fetch_add(1, std::memory_order_relaxed);
        }
        // Held by the GPU thread while it applies the writes of the pages it closed.
        std::scoped_lock lock{nx2_open_mutex};""")

replace(src / "core/device_memory.cpp", """    Common::Nx2NceStats::open_pages.store(
        VideoCommon::Nx2LiveSwitch("EDEN_NX2_NCE_OPEN_PAGES") != "0");
""", """    Common::Nx2NceStats::open_pages.store(
        VideoCommon::Nx2LiveSwitch("EDEN_NX2_NCE_OPEN_PAGES") != "0");
    Common::Nx2NceStats::open_download.store(
        VideoCommon::Nx2LiveSwitch("EDEN_NX2_NCE_OPEN_PAGES") == "2");
""")

replace(src / "core/core.cpp", """                                    if (const u64 nx2_ctr =
                                            Common::Nx2NceStats::counter_traps.exchange(0)) {""",
        """                                    if (const u64 nx2_dl =
                                            Common::Nx2NceStats::open_downloads.exchange(0)) {
                                        LOG_INFO(Core, "NX2_NCE pages downloaded before opening {} in 10 s",
                                                 nx2_dl);
                                    }
                                    if (const u64 nx2_ctr =
                                            Common::Nx2NceStats::counter_traps.exchange(0)) {""")
print("nce-open-download: EDEN_NX2_NCE_OPEN_PAGES=2 downloads before opening")
