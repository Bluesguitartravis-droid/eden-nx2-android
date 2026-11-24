#!/usr/bin/env python3
"""NCE: let the patcher see mods (IPS/built-in patches such as "Dynamic FPS by Dark").

The loader runs each static module twice: a layout pass, where the NCE patcher scans the text for
SVC/MRS to rewrite, and the load pass. The layout pass got no PatchManager, so mods were applied
only in the load pass, after the scan. Code a mod adds was never patched: Ocarina's Dynamic FPS
code cave reads CNTPCT_EL0 (traps; emulated at the guest's 31.25 MHz) and CNTFRQ_EL0 (does not
trap; the host's 19.2 MHz), so frame deltas came out 1.63x long and the game ran fast forward.
Build the PatchManager before the layout pass and give it to both passes; mods write the same
bytes in both, so the layout is unchanged."""
from pathlib import Path
import sys

path = Path(sys.argv[1]).resolve() / "src/core/loader/deconstructed_rom_directory.cpp"
text = path.read_text()

pm_block = """    std::optional<FileSys::PatchManager> pm;
    if (!exact_modules) {
        pm.emplace(metadata.GetTitleID(), system.GetFileSystemController(),
                   system.GetContentProvider());
    }
"""
scan_head = """    // Use the NSO module loader to figure out the code layout
    for (size_t i = 0; i < StaticModules.size(); i++) {"""
scan_call = """            process, system, *module_file, code_size, should_pass_arguments, false, {},
            patch_ctx.GetPatchers(), patch_ctx.GetLastIndex());"""
for needle in (pm_block, scan_head, scan_call):
    if text.count(needle) != 1:
        sys.exit(f"nce-patch-mods: expected 1 match, found {text.count(needle)}:\n{needle}")

text = text.replace(pm_block, "")
text = text.replace(scan_head, "    // NX2: mods go through the NCE patcher too (scripts/apply-nx2-nce-patch-mods.py)\n"
                    + pm_block + "\n" + scan_head)
text = text.replace(scan_call, scan_call.replace("false, {},", "false, pm,"))
path.write_text(text)
print("nce-patch-mods: layout pass gets the PatchManager")
