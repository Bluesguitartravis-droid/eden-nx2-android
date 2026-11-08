#!/usr/bin/env python3
"""Write every log line to the file at once (test builds).

A native crash loses the log lines still in memory, so a crash at load shows
only what was written well before it. Flushing each line costs little and
keeps the last line before a crash.
"""
from pathlib import Path
import sys

f = Path(sys.argv[1]) / "src/common/settings.h"
t = f.read_text()
old = 'Setting<bool> log_flush_line{linkage, false, "flush_line"'
new = 'Setting<bool> log_flush_line{linkage, true, "flush_line"'
if new in t:
    print("log flush already on"); sys.exit(0)
if old not in t:
    raise SystemExit("settings.h: log_flush_line shape changed")
f.write_text(t.replace(old, new, 1))
print("log lines flushed as written")
