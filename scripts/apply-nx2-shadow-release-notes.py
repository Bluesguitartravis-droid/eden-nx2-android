#!/usr/bin/env python3
"""Mirrored releases into diverted words are noted with their value, so acquires on those words
pass on the queued release instead of draining the split (EDEN_NX2_SHIM_ACQUIRE_PASS=1).

Build 218, Ocarina at Lon Lon Ranch: NX2_SHIM_ACQUIRES counts ~8,000 equal/mask acquires a
second, all on words Nx2MirrorShadow diverts (none on the syncpoint shim); only ~6 % already
match when checked. The rest wait for a mirrored queue-progress release the front has already sent
to the back, but the front noted that release without a value (puller.cpp:
NoteWrite only), and GpuSplit::SettledWordAt gives up on diverted words before looking at queued
notes ("Elsewhere"), so each one drains the split.

With the switch, the front notes a mirrored release into a diverted word with its payload
(NoteSemaphoreWrite, known, at its landing), and SettledWordAt treats a diverted word that has a
queued note like any other word: met by the queued release (MetByQueued: the front notes the sync
and goes on; the back runs the release first) or drained as before. Queue progress values only
grow, so the mirrored value is what the shadow will hold. The last noted release decides, as the
shadow does (a direct release that changed the guest word since is newer). Off by default;
EDEN_NX2_ACQUIRE_VERIFY=1 checks every pass.

Usage: apply-nx2-shadow-release-notes.py <upstream source root>
"""
import pathlib
import sys

root = pathlib.Path(sys.argv[1]) / "src/video_core"
MARK = "NX2 shadow release notes"


def patch(rel, edits):
    path = root / rel
    text = path.read_text()
    if MARK in text:
        print(f"{rel}: already patched")
        return
    for old, new in edits:
        if text.count(old) != 1:
            sys.exit(f"{rel}: anchor found {text.count(old)} times:\n{old}")
        text = text.replace(old, new, 1)
    path.write_text(text)
    print(f"{rel}: patched")


patch("nx2.h", [(
    "/// GPU VA in the first page that mirrors a release at `address`, or 0.\n",
    "/// " + MARK + ": EDEN_NX2_SHIM_ACQUIRE_PASS=1 notes mirrored releases into diverted words with\n"
    "/// their value and lets acquires there pass on them (GpuSplit::SettledWordAt).\n"
    "[[nodiscard]] inline bool Nx2ShadowReleaseNotes() {\n"
    "    static const bool enabled = [] {\n"
    "        const char* const value = std::getenv(\"EDEN_NX2_SHIM_ACQUIRE_PASS\");\n"
    "        return value != nullptr && value[0] == '1';\n"
    "    }();\n"
    "    return enabled;\n"
    "}\n\n"
    "/// GPU VA in the first page that mirrors a release at `address`, or 0.\n",
)])
nx2 = (root / "nx2.h").read_text()
if "#include <cstdlib>" not in nx2:
    (root / "nx2.h").write_text(nx2.replace("#include <chrono>\n", "#include <chrono>\n#include <cstdlib>\n", 1))

patch("engines/puller.cpp", [(
    "            if (const auto device = dma_pusher.memory_manager.GpuToCpuAddress(mirror);\n"
    "                device && !Nx2MirrorShadow::Get().Diverts(*device)) {\n"
    "                split.NoteUnknownWrites(*device, size, landing);\n"
    "            }\n",
    "            if (const auto device = dma_pusher.memory_manager.GpuToCpuAddress(mirror);\n"
    "                device && !Nx2MirrorShadow::Get().Diverts(*device)) {\n"
    "                split.NoteUnknownWrites(*device, size, landing);\n"
    "            } else if (device && Nx2ShadowReleaseNotes()) {\n"
    "                // " + MARK + ": the shadow will hold the payload (progress values grow).\n"
    "                split.NoteSemaphoreWrite(*device, payload, true, false, landing);\n"
    "                if (four_words) {\n"
    "                    split.NoteSemaphoreWrite(*device + 4, 0, true, false, landing);\n"
    "                    split.NoteUnknownWrites(*device + 8, 8, landing);\n"
    "                }\n"
    "            }\n",
)])

patch("gpu_split.cpp", [(
    "    if (memory.OverlapsSyncpointShim(address, sizeof(u32)) || shadow.Diverts(device_address)) {\n",
    "    // " + MARK + ": a diverted word with a valued queued release is decided by it below.\n"
    "    if (memory.OverlapsSyncpointShim(address, sizeof(u32)) ||\n"
    "        (shadow.Diverts(device_address) &&\n"
    "         !(Tegra::Nx2ShadowReleaseNotes() && QueuedSemaphoreWriteAt(device_address)))) {\n",
)])
print("mirrored releases into diverted words noted with their value (EDEN_NX2_SHIM_ACQUIRE_PASS)")
