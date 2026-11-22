#!/usr/bin/env python3
"""EDEN_NX2_SHADOW_SETTLE=1 (needs EDEN_NX2_SHIM_ACQUIRE_PASS=1): GpuSplit::SettledWordAt settles a word
Nx2MirrorShadow diverts even when no release to it is queued, at the shadow's value.

At the ranch ~55,000 acquires per 10 s are drained as "read elsewhere": a diverted word with no
queued note, whose release (from another channel) has not been sent yet. With the release notes of
apply-nx2-shadow-release-notes.py, the mirrored releases into diverted words are noted, so with no
note queued the word only changes through the checks below the bail-out (unnoted releases, shader
stores, GPU-written pages), which still apply. The acquire then comes out NotMet instead of Drain,
and EDEN_NX2_LAZY_SUSPEND=1 suspends it without draining the back. A wrong NotMet costs at most
the 1 ms valve (CanResumeAcquire drains and re-reads); EDEN_NX2_ACQUIRE_VERIFY=1 reports wrong passes."""
from pathlib import Path
import sys

root = Path(sys.argv[1]).resolve() / "src/video_core"

nx2 = root / "nx2.h"
t = nx2.read_text()
if "Nx2ShadowSettle" not in t:
    anchor = "/// GPU VA in the first page that mirrors a release at `address`, or 0."
    if t.count(anchor) != 1:
        sys.exit("nx2.h: anchor not found (apply-nx2-shadow-release-notes.py first?)")
    t = t.replace(anchor, """/// EDEN_NX2_SHADOW_SETTLE=1 (with EDEN_NX2_SHIM_ACQUIRE_PASS=1): a diverted word with no queued note
/// settles at the shadow's value (scripts/apply-nx2-shadow-settle.py).
[[nodiscard]] inline bool Nx2ShadowSettle() {
    static const bool enabled = [] {
        const char* const value = std::getenv("EDEN_NX2_SHADOW_SETTLE");
        return value != nullptr && value[0] == '1';
    }();
    return enabled && Nx2ShadowReleaseNotes();
}

""" + anchor, 1)
    nx2.write_text(t)

split = root / "gpu_split.cpp"
t = split.read_text()
old = """        (shadow.Diverts(device_address) &&
         !(Tegra::Nx2ShadowReleaseNotes() && QueuedSemaphoreWriteAt(device_address)))) {"""
new = """        (shadow.Diverts(device_address) && !Tegra::Nx2ShadowSettle() &&
         !(Tegra::Nx2ShadowReleaseNotes() && QueuedSemaphoreWriteAt(device_address)))) {"""
if "Nx2ShadowSettle" not in t:
    if t.count(old) != 1:
        sys.exit("gpu_split.cpp: SettledWordAt's diverted-word bail-out not found")
    split.write_text(t.replace(old, new, 1))
print("gpu_split.cpp: EDEN_NX2_SHADOW_SETTLE=1 settles diverted words at the shadow's value")
