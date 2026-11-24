#!/usr/bin/env python3
"""EDEN_NX2_NCE_OPEN_PAGES=0 is read at each game boot from the switches file (it was read once
per process from the environment), so it can be A/B-tested by relaunching the game.

On 248 Ocarina boots on NCE but the world renders white at 5 fps with the GPU 94 % busy (HUD
fine). Open pages (a write fault on a GPU-cached page keeps a copy and opens the page; the GPU
learns of the writes when the pages are gathered) is the NCE path most likely to hide CPU writes
from the GPU since the rebase."""
from pathlib import Path
import sys

src = Path(sys.argv[1]).resolve() / "src"


def replace(path, old, new):
    text = path.read_text()
    if text.count(old) != 1:
        sys.exit(f"nce-open-pages-live: {path.name}: expected 1 match, found {text.count(old)}")
    path.write_text(text.replace(old, new))


replace(src / "common/nx2_nce_stats.h", "inline std::atomic<bool> skip_undefined{};\n",
        "inline std::atomic<bool> skip_undefined{};\n"
        "// EDEN_NX2_NCE_OPEN_PAGES (default on), set at each game boot.\n"
        "inline std::atomic<bool> open_pages{true};\n")

replace(src / "core/memory.cpp", """        static const bool enabled = [] {
            const char* value = std::getenv("EDEN_NX2_NCE_OPEN_PAGES");
            return value == nullptr || value[0] != '0';
        }();
        if (!enabled || ptr == nullptr || !current_page_table->fastmem_arena ||""",
        """        const bool enabled = Common::Nx2NceStats::open_pages.load(std::memory_order_relaxed);
        if (!enabled || ptr == nullptr || !current_page_table->fastmem_arena ||""")
text = (src / "core/memory.cpp").read_text()
if '#include "common/nx2_nce_stats.h"' not in text:
    (src / "core/memory.cpp").write_text(
        text.replace('#include "common/settings.h"\n',
                     '#include "common/nx2_nce_stats.h"\n#include "common/settings.h"\n', 1))

replace(src / "core/device_memory.cpp", """    Common::Nx2NceStats::skip_undefined.store(
        VideoCommon::Nx2LiveSwitch("EDEN_NX2_NCE_SKIP_UNDEFINED") == "1");
""", """    Common::Nx2NceStats::skip_undefined.store(
        VideoCommon::Nx2LiveSwitch("EDEN_NX2_NCE_SKIP_UNDEFINED") == "1");
    Common::Nx2NceStats::open_pages.store(
        VideoCommon::Nx2LiveSwitch("EDEN_NX2_NCE_OPEN_PAGES") != "0");
    LOG_INFO(Core, "NX2 NCE open pages: {}", Common::Nx2NceStats::open_pages.load());
""")
print("nce-open-pages-live: EDEN_NX2_NCE_OPEN_PAGES read at each game boot")
