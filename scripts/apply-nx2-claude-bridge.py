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


def patch(relative, old, new, mark=MARK):
    path = app / relative
    text = path.read_text()
    if mark in text:
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
      "        // " + MARK + ": the game for restart_app, and Claude on the game screen.\n"
      "        nx2RememberGame(intent)\n"
      "        runCatching { io.github.zortos293.lichii.nx2.Nx2Overlay.attach(this) }\n")

# A game started while one is running (swap) arrives through onNewIntent, not onCreate.
patch("java/io/github/zortos293/lichii/activities/EmulationActivity.kt",
      "    override fun onNewIntent(intent: Intent) {\n        super.onNewIntent(intent)\n",
      "    // " + MARK + "\n"
      "    private fun nx2RememberGame(source: Intent) {\n"
      "        runCatching {\n"
      "            val path = source.data?.toString() ?: source.extras?.let {\n"
      "                BundleCompat.getParcelable(it, EXTRA_SELECTED_GAME, Game::class.java)\n"
      "            }?.path\n"
      "            if (!path.isNullOrEmpty()) io.github.zortos293.lichii.nx2.Nx2Bridge.rememberGame(path)\n"
      "        }\n"
      "    }\n\n"
      "    override fun onNewIntent(intent: Intent) {\n        super.onNewIntent(intent)\n"
      "        nx2RememberGame(intent)\n", mark="private fun nx2RememberGame")

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
      "        <service\n"
      "            android:name=\"io.github.zortos293.lichii.nx2.Nx2LabService\"\n"
      "            android:exported=\"false\"\n"
      "            android:foregroundServiceType=\"specialUse\">\n"
      "            <property\n"
      "                android:name=\"android.app.PROPERTY_SPECIAL_USE_FGS_SUBTYPE\"\n"
      "                android:value=\"Claude Lab: debugging connection to Claude while the player tests games\" />\n"
      "        </service>\n"
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

# Live frame statistics for the bridge's watch tool: a third frame window next to "recent" (the
# 10 s NX2_FRAMES report) and "session", read and reset only by the bridge, so it disturbs
# neither those reports nor the FPS overlay (getPerfStats resets the core's own counters).
session_h = root / "src/common/nx2_session.h"
text = session_h.read_text()
if MARK not in text:
    for old, new in [
        ("inline FrameWindow session; ///< Since the game started\n",
         "inline FrameWindow session; ///< Since the game started\n"
         "inline FrameWindow bridge;  ///< " + MARK + ": since the bridge last asked\n"),
        ("    recent.Add(us);\n    session.Add(us);\n",
         "    recent.Add(us);\n    session.Add(us);\n    bridge.Add(us);\n"),
    ]:
        if text.count(old) != 1:
            raise SystemExit(f"nx2_session.h: anchor found {text.count(old)} times:\n{old}")
        text = text.replace(old, new, 1)
    session_h.write_text(text)
    print("patched: nx2_session.h")

native = app / "jni/native.cpp"
text = native.read_text()
if MARK not in text:
    if '#include "common/nx2_session.h"' not in text:
        raise SystemExit("native.cpp: nx2_session.h not included (run apply-nx2-diagnostics.py first)")
    native.write_text(text + """
// """ + MARK + """: frame statistics since the bridge last asked ("" when none were presented).
extern "C" JNIEXPORT jstring JNICALL
Java_io_github_zortos293_lichii_nx2_Nx2Bridge_nativeLiveFrames(JNIEnv* env, jclass) {
    const std::string text = Common::Nx2Session::bridge.Describe(true);
    return env->NewStringUTF(text.c_str());
}
""")
    print("patched: native.cpp")

rules = root / "src/android/app/proguard-rules.pro"
text = rules.read_text()
if MARK not in text:
    rules.write_text(text + "\n# " + MARK + ": native method looked up by name\n"
                     "-keep class io.github.zortos293.lichii.nx2.Nx2Bridge { native <methods>; }\n")
    print("patched: proguard-rules.pro")

print("Claude Lab installed")
