#!/usr/bin/env python3
"""Claude Lab: an MCP server inside Eden that Claude reaches as a custom connector.

The player turns it on in Settings -> Claude Lab, pastes an ngrok authtoken, and adds the shown
address (https://<their ngrok domain>/mcp/<secret>) as a custom connector on claude.ai. Claude can
then read performance and logs, change settings and switches, restart the app, take screenshots
and press buttons. Off by default; listens on 127.0.0.1 only, behind the secret path.

Installs android_overlay/.../nx2/*.kt, the Lab and restart activities, the Home settings entry,
and libnx2tunnel.so (tools/nx2tunnel, built by the workflow before this script) when present.
Run after apply-nx2-flight-recorder.py.
Usage: python3 scripts/apply-nx2-claude-bridge.py <upstream-root> [libnx2tunnel.so]
"""
from pathlib import Path
import shutil
import sys

root = Path(sys.argv[1]).resolve()
repo = Path(__file__).resolve().parent.parent
app = root / "src/android/app/src/main"
kt = app / "java/io/github/zortos293/lichii"
MARK = "NX2 Claude Lab"

src = repo / "android_overlay/src/main/java/io/github/zortos293/lichii/nx2"
dst = kt / "nx2"
dst.mkdir(exist_ok=True)
for file in sorted(src.glob("*.kt")):
    shutil.copyfile(file, dst / file.name)
    print("copied", file.name)

if len(sys.argv) > 2 and Path(sys.argv[2]).is_file():
    libs = app / "jniLibs/arm64-v8a"
    libs.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(sys.argv[2], libs / "libnx2tunnel.so")
    print("installed libnx2tunnel.so")
else:
    print("WARNING: no libnx2tunnel.so; the Lab works only on 127.0.0.1")


def patch(relative, old, new):
    path = app / relative
    text = path.read_text()
    if MARK in text:
        print("already patched:", relative)
        return
    if text.count(old) != 1:
        raise SystemExit(f"{relative}: anchor found {text.count(old)} times:\n{old}")
    path.write_text(text.replace(old, new, 1))
    print("patched:", relative)


# The restart helper runs in its own process (":nx2restart"): the app's start-up (native library,
# log rotation, flight recorder recovery, the bridge's port) must not run there.
patch("java/io/github/zortos293/lichii/LichiiApplication.kt",
      "        super.onCreate()\n        application = this\n"
      "        io.github.zortos293.lichii.utils.Nx2FlightRecorder.initialize(this)\n",
      "        super.onCreate()\n"
      "        // " + MARK + ": nothing else runs in the restart helper's process.\n"
      "        if (io.github.zortos293.lichii.nx2.Nx2Bridge.isHelperProcess(this)) return\n"
      "        application = this\n"
      "        io.github.zortos293.lichii.utils.Nx2FlightRecorder.initialize(this)\n"
      "        // Starts the bridge only when the player turned it on.\n"
      "        runCatching { io.github.zortos293.lichii.nx2.Nx2Bridge.initialize(this) }\n")

patch("java/io/github/zortos293/lichii/activities/EmulationActivity.kt",
      "        io.github.zortos293.lichii.utils.Nx2FlightRecorder.start(this)\n",
      "        io.github.zortos293.lichii.utils.Nx2FlightRecorder.start(this)\n"
      "        // " + MARK + ": remembered for the bridge's restart_app.\n"
      "        runCatching {\n"
      "            io.github.zortos293.lichii.nx2.Nx2Bridge.rememberGame(intent.data?.toString()\n"
      "                ?: intent.extras?.let {\n"
      "                    BundleCompat.getParcelable(it, EXTRA_SELECTED_GAME, Game::class.java)\n"
      "                }?.path)\n"
      "        }\n")

patch("AndroidManifest.xml",
      "    </application>\n",
      "        <!-- " + MARK + " -->\n"
      "        <activity\n"
      "            android:name=\"io.github.zortos293.lichii.nx2.Nx2LabActivity\"\n"
      "            android:exported=\"false\"\n"
      "            android:label=\"Claude Lab\"\n"
      "            android:theme=\"@android:style/Theme.Material\" />\n"
      "        <activity\n"
      "            android:name=\"io.github.zortos293.lichii.nx2.Nx2RestartActivity\"\n"
      "            android:exported=\"false\"\n"
      "            android:process=\":nx2restart\"\n"
      "            android:excludeFromRecents=\"true\"\n"
      "            android:noHistory=\"true\"\n"
      "            android:theme=\"@android:style/Theme.Translucent.NoTitleBar\" />\n"
      "    </application>\n")

patch("res/values/strings.xml",
      "</resources>",
      "    <!-- " + MARK + " -->\n"
      "    <string name=\"nx2_claude_lab\">Claude Lab</string>\n"
      "    <string name=\"nx2_claude_lab_description\">Let Claude test performance and settings on this device</string>\n"
      "</resources>")

patch("java/io/github/zortos293/lichii/fragments/HomeSettingsFragment.kt",
      "        val optionsList: MutableList<HomeSetting> = mutableListOf<HomeSetting>().apply {\n",
      "        val optionsList: MutableList<HomeSetting> = mutableListOf<HomeSetting>().apply {\n"
      "            // " + MARK + "\n"
      "            add(\n"
      "                HomeSetting(\n"
      "                    R.string.nx2_claude_lab,\n"
      "                    R.string.nx2_claude_lab_description,\n"
      "                    R.drawable.ic_code,\n"
      "                    {\n"
      "                        startActivity(Intent(requireContext(),\n"
      "                            io.github.zortos293.lichii.nx2.Nx2LabActivity::class.java))\n"
      "                    }\n"
      "                )\n"
      "            )\n")
print("Claude Lab installed")
