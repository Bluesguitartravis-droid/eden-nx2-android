#!/usr/bin/env python3
"""Test builds: log the Switch 2 video decoder's per-frame trace (EDEN_NX2_VDEC_TRACE) without
the switches file. Ocarina's opening video stops after its first frame."""
from pathlib import Path
import sys

f = Path(sys.argv[1]).resolve() / "src/core/hle/service/nx2_gsv.h"
t = f.read_text()
old = '    static const bool enabled = std::getenv("EDEN_NX2_VDEC_TRACE") != nullptr;\n'
new = '    static const bool enabled = true; // test builds: always trace\n'
if new not in t:
    if old not in t:
        raise SystemExit("Nx2VideoTraceEnabled shape changed")
    f.write_text(t.replace(old, new, 1))
print("video decoder trace on")
