#!/usr/bin/env python3
"""The NX2_HANG watchdog only reports a missing present; its dump (interrupting every guest core
to sample registers, backtraces and memory, then listing every thread's context) runs only with
EDEN_NX2_HANG_DUMP=1 in the switches file.

A boot shows no present for well over 10 s, so the dump fired during normal boots: once on
Dynarmic (first present ~40 s), twice on NCE. On NCE the log stopped at the second dump's core
interrupts every time (3 of 3, builds 226 and 244) and the boot never finished: interrupting cores
that run native guest code mid-boot wedged them. The intermittent Dynarmic boot hangs since the
rebase also came at a dump."""
from pathlib import Path
import sys

path = Path(sys.argv[1]).resolve() / "src/core/core.cpp"
text = path.read_text()
old = """                    LOG_WARNING(Core, "NX2_HANG no present for {} ms (dump {}/6, nce={})", idle, dumps,
                                Settings::IsNceEnabled());
"""
new = """                    LOG_WARNING(Core, "NX2_HANG no present for {} ms (dump {}/6, nce={})", idle, dumps,
                                Settings::IsNceEnabled());
                    // NX2: the dump interrupts every core; opt-in (scripts/apply-nx2-hang-dump-optin.py)
                    if (std::getenv("EDEN_NX2_HANG_DUMP") == nullptr) {
                        continue;
                    }
"""
if text.count(old) != 1:
    sys.exit("hang-dump-optin: watchdog dump not found")
path.write_text(text.replace(old, new))
print("hang-dump-optin: NX2_HANG dumps need EDEN_NX2_HANG_DUMP=1")
