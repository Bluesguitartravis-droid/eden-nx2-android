#!/usr/bin/env python3
"""Acquire switch defaults and EDEN_NX2_SETTLED_NOT_MET.

- EDEN_NX2_SHIM_ACQUIRE_PASS and EDEN_NX2_SHADOW_SETTLE default on (=0 turns them off): 219-224 ran
  them with EDEN_NX2_ACQUIRE_VERIFY=1 at the ranch and in Hyrule Field with 0 wrong passes.
- EDEN_NX2_SETTLED_NOT_MET=1: an equal or mask acquire whose word settles (SettledWordAt) at a value
  that does not meet it is NotMet, as a reach (GEQUAL) acquire whose queued release falls short
  already is, instead of Drain. Upstream drains them: at Hyrule Field ~65,000 per 10 s, the largest
  group of drains, none of them "may still change". NotMet drains too unless EDEN_NX2_LAZY_SUSPEND=1."""
from pathlib import Path
import sys

root = Path(sys.argv[1]).resolve() / "src/video_core"

def edit(path, old, new, count=1):
    text = path.read_text()
    if text.count(old) != count:
        sys.exit(f"{path.name}: expected {count} match(es) for {old[:70]!r}, found {text.count(old)}")
    path.write_text(text.replace(old, new))

nx2 = root / "nx2.h"
for name in ("EDEN_NX2_SHIM_ACQUIRE_PASS", "EDEN_NX2_SHADOW_SETTLE"):
    edit(nx2, f"""        const char* const value = std::getenv("{name}");
        return value != nullptr && value[0] == '1';""",
         f"""        const char* const value = std::getenv("{name}");
        return value == nullptr || value[0] != '0'; // On unless {name}=0""")

puller = root / "engines/puller.cpp"
edit(puller, """        const char* const value{std::getenv("EDEN_NX2_SHIM_ACQUIRE_PASS")};
        return value != nullptr && value[0] == '1';""",
     """        const char* const value{std::getenv("EDEN_NX2_SHIM_ACQUIRE_PASS")};
        return value == nullptr || value[0] != '0'; // On unless EDEN_NX2_SHIM_ACQUIRE_PASS=0""")

edit(puller, """AcquireCheck CheckWithoutDrain(DmaPusher& dma_pusher, const Puller::PendingAcquire& acquire,
                               u64& release) {""",
     """// EDEN_NX2_SETTLED_NOT_MET=1 (scripts/apply-nx2-settled-not-met.py): an equal or mask acquire
// whose word settles at a value that does not meet it is NotMet instead of Drain.
bool Nx2SettledNotMet() {
    static const bool enabled{[] {
        const char* const value{std::getenv("EDEN_NX2_SETTLED_NOT_MET")};
        return value != nullptr && value[0] == '1';
    }()};
    return enabled;
}

AcquireCheck CheckWithoutDrain(DmaPusher& dma_pusher, const Puller::PendingAcquire& acquire,
                               u64& release) {""")
edit(puller, """            release = *settled->release;
            return AcquireCheck::MetByQueued;
        }
        if (!settled && why == VideoCommon::AcquireUnsettled::Elsewhere) {""",
     """            release = *settled->release;
            return AcquireCheck::MetByQueued;
        }
        if (settled && Nx2SettledNotMet()) {
            // What every write sent to the word leaves there falls short: only a release not sent
            // yet meets it, as for a reach acquire whose queued release falls short.
            return AcquireCheck::NotMet;
        }
        if (!settled && why == VideoCommon::AcquireUnsettled::Elsewhere) {""")
print("acquire switches on by default; EDEN_NX2_SETTLED_NOT_MET=1 makes settled unmet equal/mask acquires NotMet")
