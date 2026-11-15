#!/usr/bin/env python3
from pathlib import Path
import shutil
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: apply-snapshot-packer.py <upstream-dir>")

root = Path(sys.argv[1]).resolve()
harness = Path(__file__).resolve().parent.parent
src = root / "src" / "android" / "app" / "src" / "main"

overlay = harness / "android_overlay" / "SnapshotPackerActivity.kt"
dest = src / "java" / "org" / "yuzu" / "yuzu_emu" / "features" / "packer" / "SnapshotPackerActivity.kt"
dest.parent.mkdir(parents=True, exist_ok=True)
shutil.copy2(overlay, dest)

home = src / "java" / "org" / "yuzu" / "yuzu_emu" / "fragments" / "HomeSettingsFragment.kt"
home_text = home.read_text()
packer_import = "import io.github.zortos293.lichii.features.packer.SnapshotPackerActivity\n"
if packer_import not in home_text:
    anchor = "import io.github.zortos293.lichii.features.settings.model.Settings\n"
    if anchor not in home_text:
        raise SystemExit(f"Could not find HomeSettings import anchor in {home}")
    home_text = home_text.replace(anchor, packer_import + anchor, 1)

tile = '''            add(
                HomeSetting(
                    R.string.snapshot_packer,
                    R.string.snapshot_packer_description,
                    R.drawable.ic_install,
                    {
                        startActivity(Intent(requireContext(), SnapshotPackerActivity::class.java))
                    }
                )
            )
'''
if "R.string.snapshot_packer," not in home_text:
    marker = '''            add(
                HomeSetting(
                    R.string.verify_installed_content,
'''
    if marker not in home_text:
        raise SystemExit(f"Could not find HomeSettings insertion point in {home}")
    home_text = home_text.replace(marker, tile + marker, 1)
home.write_text(home_text)

manifest = src / "AndroidManifest.xml"
manifest_text = manifest.read_text()
activity = '''        <activity
            android:name="io.github.zortos293.lichii.features.packer.SnapshotPackerActivity"
            android:theme="@style/Theme.Yuzu.Main"
            android:label="@string/snapshot_packer"
            android:exported="false" />

'''
if "features.packer.SnapshotPackerActivity" not in manifest_text:
    marker = '''        <activity
            android:name="io.github.zortos293.lichii.features.settings.ui.SettingsActivity"
'''
    if marker not in manifest_text:
        raise SystemExit(f"Could not find manifest insertion point in {manifest}")
    manifest_text = manifest_text.replace(marker, activity + marker, 1)
manifest.write_text(manifest_text)

strings = src / "res" / "values" / "strings.xml"
strings_text = strings.read_text()
string_block = '''
    <!-- Plaintext extracted-snapshot packer. Does not decrypt retail containers. -->
    <string name="snapshot_packer">Pack extracted game to NSP</string>
    <string name="snapshot_packer_description">Package ExeFS and an effective, optionally base+update merged RomFS into an experimental plaintext NSP</string>
    <string name="snapshot_packer_intro">Choose a complete, already-extracted game folder. For updates, select the parent containing base/{exefs,romfs} and updates/&lt;version&gt;/{exefs,romfs}; the packer selects the latest update and overlays its RomFS on the base. It creates an unsigned emulator NSP and keeps main.npdm beside it for Switch 2 detection. A plain exefs/romfs snapshot also works.</string>
    <string name="snapshot_packer_choose_source">Choose extracted game folder</string>
    <string name="snapshot_packer_choose_output">Choose output folder and build NSP</string>
    <string name="snapshot_packer_no_source">No snapshot selected</string>
    <string name="snapshot_packer_title_hint">Game title</string>
    <string name="snapshot_packer_idle">Select a source folder to begin.</string>
    <string name="snapshot_packer_scanning">Scanning for complete ExeFS + RomFS snapshots…</string>
    <string name="snapshot_packer_source_ready">Snapshot: %1$s\nTitle ID: %2$s\nVersion: %3$d</string>
    <string name="snapshot_packer_ready">Ready. Choose an output folder to build the package.</string>
    <string name="snapshot_packer_preparing">Preparing plaintext NSP…</string>
    <string name="snapshot_packer_cancelling">Cancelling after the current file operation…</string>
    <string name="snapshot_packer_cancelled">Packaging cancelled.</string>
    <string name="snapshot_packer_scan_failed">Could not scan the selected snapshot.</string>
    <string name="snapshot_packer_failed">Could not build the NSP.</string>
    <string name="snapshot_packer_complete_title">NSP created</string>
    <string name="snapshot_packer_complete">Created: %1$s</string>
    <string name="snapshot_packer_complete_message">Created %1$s. The package folder also contains main.npdm for Switch 2 mode detection. Add the output folder to your game folders or open the NSP normally.</string>
    <string name="snapshot_packer_limitations">Lawful extracted snapshots only. This tool does not decrypt encrypted NSP/XCI files, download games, generate retail tickets/signatures, or reconstruct cartridge authentication. Output is an unsigned plaintext package for compatible emulators.</string>
'''
if 'name="snapshot_packer"' not in strings_text:
    if "</resources>" not in strings_text:
        raise SystemExit(f"Could not find closing resources tag in {strings}")
    strings_text = strings_text.replace("</resources>", string_block + "\n</resources>", 1)
strings.write_text(strings_text)

license_text = """MIT License

Copyright (c) 2026 Switch 2 XCI Packager contributors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the \"Software\"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED \"AS IS\", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""
license_path = src / "assets" / "licenses" / "NSP-Box-LICENSE.txt"
license_path.parent.mkdir(parents=True, exist_ok=True)
license_path.write_text(license_text)

print(f"Installed snapshot packer: {dest}")
print(f"Patched settings tile: {home}")
print(f"Patched manifest: {manifest}")
print(f"Patched strings: {strings}")
print(f"Installed MIT notice: {license_path}")
