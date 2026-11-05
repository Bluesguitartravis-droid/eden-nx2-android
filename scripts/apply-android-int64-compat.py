#!/usr/bin/env python3
"""Android NDK: std::chrono durations use long long while s64 (int64_t) is long,
so std::max(s64, duration.count()) has no matching overload. Cast explicitly."""
from pathlib import Path
import sys

root = Path(sys.argv[1])
f = root / "src/core/hle/service/nx2_gsv.h"
old = "(std::max)(vsync_state->trace_max_late, late.count());"
new = "(std::max)(vsync_state->trace_max_late, static_cast<s64>(late.count()));"
t = f.read_text() if f.exists() else ""
if old in t:
    f.write_text(t.replace(old, new))
    print("nx2_gsv.h trace_max_late int64 compat applied")
else:
    print("nx2_gsv.h trace_max_late pattern absent; skipping")
