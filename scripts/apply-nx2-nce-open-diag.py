#!/usr/bin/env python3
"""Diagnostics for NCE open pages (Ocarina's world renders white with them; the pause menu is fine).

- EDEN_NX2_NCE_OPEN_WHOLE=1 (read at each game boot): a gathered page reports the whole page as
  written instead of the changed 4-byte runs. Only correct with EDEN_NX2_NCE_OPEN_PAGES=2 (the
  GPU's bytes downloaded before opening). If the world renders with it, the run-by-run handoff
  is at fault; if not, the open window itself is.
- EDEN_NX2_NCE_OPEN_LOG=<n> (live; each new value starts a new count): log the next n gathered
  pages: guest page, device address, changed runs and bytes, first runs."""
from pathlib import Path
import sys

src = Path(sys.argv[1]).resolve() / "src"


def replace(path, old, new):
    text = path.read_text()
    if text.count(old) != 1:
        sys.exit(f"nce-open-diag: {path.name}: expected 1 match, found {text.count(old)}:\n{old}")
    path.write_text(text.replace(old, new))


replace(src / "common/nx2_nce_stats.h", "inline std::atomic<bool> open_pages{true};\n",
        "inline std::atomic<bool> open_pages{true};\n"
        "// EDEN_NX2_NCE_OPEN_WHOLE: a gathered page reports itself whole (diagnostic).\n"
        "inline std::atomic<bool> open_whole{false};\n")

replace(src / "core/device_memory.cpp", """    Common::Nx2NceStats::open_download.store(
        VideoCommon::Nx2LiveSwitch("EDEN_NX2_NCE_OPEN_PAGES") == "2");
""", """    Common::Nx2NceStats::open_download.store(
        VideoCommon::Nx2LiveSwitch("EDEN_NX2_NCE_OPEN_PAGES") == "2");
    Common::Nx2NceStats::open_whole.store(
        VideoCommon::Nx2LiveSwitch("EDEN_NX2_NCE_OPEN_WHOLE") == "1");
""")

memory = src / "core/memory.cpp"
replace(memory, """            const u8* const now = entry.ptr;
            const u8* const before = entry.before.get();
            size_t run_begin = none;
            for (size_t offset = 0; offset <= LICHII_PAGESIZE; offset += 4) {""", """            const u8* const now = entry.ptr;
            const u8* const before = entry.before.get();
            // NX2 diagnostics (scripts/apply-nx2-nce-open-diag.py)
            static std::string nx2_log_value;
            static u64 nx2_log_left = 0;
            {
                const std::string value = VideoCommon::Nx2LiveSwitch("EDEN_NX2_NCE_OPEN_LOG");
                if (value != nx2_log_value) {
                    nx2_log_value = value;
                    nx2_log_left = std::strtoull(value.c_str(), nullptr, 10);
                }
            }
            if (nx2_log_left > 0) {
                --nx2_log_left;
                size_t runs = 0;
                size_t bytes = 0;
                std::string first;
                size_t begin = none;
                for (size_t offset = 0; offset <= LICHII_PAGESIZE; offset += 4) {
                    if (offset < LICHII_PAGESIZE &&
                        std::memcmp(now + offset, before + offset, 4) != 0) {
                        if (begin == none) {
                            begin = offset;
                        }
                        continue;
                    }
                    if (begin != none) {
                        ++runs;
                        bytes += offset - begin;
                        if (runs <= 4) {
                            first += fmt::format(" +{:#x}:{}", begin, offset - begin);
                        }
                        begin = none;
                    }
                }
                DAddr device = 0;
                gpu_device_memory->ApplyOpOnPointer(now, nx2_open_scratch,
                                                    [&](DAddr address) { device = address; });
                LOG_INFO(HW_Memory, "NX2_OPEN_LOG page {:#x} device {:#x}: {} runs, {} bytes{}",
                         page << LICHII_PAGEBITS, device, runs, bytes, first);
            }
            if (Common::Nx2NceStats::open_whole.load(std::memory_order_relaxed)) {
                gpu_device_memory->ApplyOpOnPointer(
                    now, nx2_open_scratch,
                    [&](DAddr address) { callback(address, LICHII_PAGESIZE); });
                continue;
            }
            size_t run_begin = none;
            for (size_t offset = 0; offset <= LICHII_PAGESIZE; offset += 4) {""")
text = memory.read_text()
if '#include "video_core/nx2_live_switch.h"' not in text:
    first = text.index("#include ")
    memory.write_text(text[:first] + '#include "video_core/nx2_live_switch.h"\n' + text[first:])
print("nce-open-diag: EDEN_NX2_NCE_OPEN_WHOLE and EDEN_NX2_NCE_OPEN_LOG added")
