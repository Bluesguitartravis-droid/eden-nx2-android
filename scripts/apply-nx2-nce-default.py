#!/usr/bin/env python3
"""cpu_backend defaults to NCE again where NCE is built (Android).

Lichii 523e5048 (#2246, 2026-11-19) made Dynarmic the default on ARM64. Our builds up to 219 ran
Ocarina on NCE by default; on 221-225 (Dynarmic) about 5 of 13 boots hung before the first frame
(one guest core at 997 ms/s, GPU idle, no GPU semaphore waits). With NCE: 3 of 3 boots fine, same
FPS in Hyrule Field (18.7-19.1). A cpu_backend the player set stays as set."""
from pathlib import Path
import sys

f = Path(sys.argv[1]).resolve() / "src/common/settings.h"
t = f.read_text()
old = """    SwitchableSetting<CpuBackend, true> cpu_backend{linkage,
                                                    CpuBackend::Dynarmic,
                                                    CpuBackend::Dynarmic,"""
new = """    SwitchableSetting<CpuBackend, true> cpu_backend{linkage,
#ifdef HAS_NCE
                                                    CpuBackend::Nce, // NX2: Dynarmic hangs Ocarina's boot
#else
                                                    CpuBackend::Dynarmic,
#endif
                                                    CpuBackend::Dynarmic,"""
if "NX2: Dynarmic hangs" not in t:
    if t.count(old) != 1:
        sys.exit("settings.h: cpu_backend declaration not found")
    f.write_text(t.replace(old, new, 1))
print("settings.h: cpu_backend defaults to NCE where it is built")
