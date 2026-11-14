#!/usr/bin/env python3
"""When the replacement upscale draws over Fire Emblem's DLSS output (the default:
"Use the game's own DLSS" is off, or resolution scaling is on), the network's middle
passes still ran, emulating every tensor-core matrix multiply only for the result to be
thrown away. Skip them once they are known (after the first evaluation). The prepass
still runs (it carries the colour, motion vectors, depth and jitter the replacement
reads), and the output pass is still replaced. EDEN_NX2_DLSS_RUN_NETWORK=1 restores the
old behaviour."""
from pathlib import Path
import sys

root = Path(sys.argv[1]).resolve()
f = root / "src/video_core/engines/kepler_compute.cpp"
t = f.read_text()
old = """    return DlssNetworkDroppedForGood() && !passes->IsPrepass(native_key) &&
           dlss_chain.programs.contains(native_key);
}"""
# Lichii (2026-11-13) skips the middle passes itself once the network is dropped for good; the
# replacement chosen by the setting (a blocker) still ran them.
new = """    static const bool nx2_run_network = [] {
        const char* v = std::getenv("EDEN_NX2_DLSS_RUN_NETWORK");
        return v && v[0] == '1';
    }();
    const bool nx2_skip = nx2_run_network ? DlssNetworkDroppedForGood()
                                          : replacement && !recheck_after_dispatch;
    const bool nx2_skipped = nx2_skip && !passes->IsPrepass(native_key) &&
                             dlss_chain.programs.contains(native_key);
    if (nx2_skipped) {
        static bool nx2_logged = false;
        if (!nx2_logged) {
            nx2_logged = true;
            LOG_INFO(HW_GPU, "NX2: replacement upscale active; skipping the game's DLSS network passes");
        }
    }
    return nx2_skipped;
}"""
if "EDEN_NX2_DLSS_RUN_NETWORK" not in t:
    if old not in t:
        raise SystemExit("ProcessGameDlss tail not found")
    t = t.replace(old, new, 1)
    if "#include <cstdlib>" not in t:
        t = "#include <cstdlib>\n" + t
    f.write_text(t)
print("DLSS network skip applied")
