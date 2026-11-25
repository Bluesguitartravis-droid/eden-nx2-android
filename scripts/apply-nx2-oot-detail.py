#!/usr/bin/env python3
"""EDEN_NX2_OOT_DETAIL (read at each game boot from the switches file): Ocarina of Time with fewer
ground layers (reduced: no moss or stones; lowest: also no small undergrowth; the grass stays).
276: EDEN_NX2_OOT_PARAM edits any of the game's BYML parameter files, EDEN_NX2_OOT_LIST lists them.
Adds src/core/file_sys/nx2_oot_detail.h (ported from Anka Native's detail.h, MIT) and a RomFS layer
of its own in PatchManager::ApplyLayeredFS, under the player's own mods and over the game's files.
The layer is built even when the game has no mod folder."""
from pathlib import Path
import shutil
import sys

src = Path(sys.argv[1]).resolve() / "src"
here = Path(__file__).resolve().parent
shutil.copyfile(here / "nx2_oot_detail.h", src / "core/file_sys/nx2_oot_detail.h")

pm = src / "core/file_sys/patch_manager.cpp"
text = pm.read_text()


def sub(old, new, count=1):
    global text
    if text.count(old) != count:
        sys.exit(f"apply-nx2-oot-detail: expected {count} of {old[:80]!r}, found {text.count(old)}")
    text = text.replace(old, new)


sub('#include "core/file_sys/ips_layer.h"\n',
    '#include "core/file_sys/ips_layer.h"\n#include "core/file_sys/nx2_oot_detail.h"\n')

sub('''    const auto load_dir = GetModificationLoadRoot();
    const auto sdmc_load_dir = GetModificationLoadRoot(true);
    if ((type != ContentRecordType::Program && type != ContentRecordType::Data &&
         type != ContentRecordType::HtmlDocument) ||
        (load_dir == nullptr && sdmc_load_dir == nullptr)) {
        return;
    }
''', '''    const auto load_dir = GetModificationLoadRoot();
    const auto sdmc_load_dir = GetModificationLoadRoot(true);
    const auto nx2_detail = type == ContentRecordType::Program ? Nx2OotDetail::Wanted(title_id)
                                                               : Nx2OotDetail::Request{};
    if ((type != ContentRecordType::Program && type != ContentRecordType::Data &&
         type != ContentRecordType::HtmlDocument) ||
        (load_dir == nullptr && sdmc_load_dir == nullptr && !nx2_detail.Any())) {
        return;
    }
''')

sub('''    // When there are no layers to apply, return early as there is no need to rebuild the RomFS
    if (layers.empty() && layers_ext.empty()) {
        return;
    }

    auto extracted = ExtractRomFS(romfs);
    if (extracted == nullptr) {
        return;
    }
''', '''    VirtualDir nx2_extracted;
    if (nx2_detail.Any()) {
        nx2_extracted = ExtractRomFS(romfs);
        if (auto detail = Nx2OotDetail::BuildOverlay(nx2_extracted, nx2_detail)) {
            layers.emplace_back(std::move(detail));
            mods.push_back("NX2 game parameters (EDEN_NX2_OOT_DETAIL / EDEN_NX2_OOT_PARAM)");
        }
    }

    // When there are no layers to apply, return early as there is no need to rebuild the RomFS
    if (layers.empty() && layers_ext.empty()) {
        return;
    }

    auto extracted = nx2_extracted != nullptr ? std::move(nx2_extracted) : ExtractRomFS(romfs);
    if (extracted == nullptr) {
        return;
    }
''')

pm.write_text(text)
print("apply-nx2-oot-detail: Ocarina ground-detail RomFS layer added")
