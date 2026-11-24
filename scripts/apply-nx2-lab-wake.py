#!/usr/bin/env python3
"""Claude Lab: wake the screen and show Eden over the lock screen for the Lab's own starts.

While Travis is away, the Odin's screen times out at Eden's game list (Eden keeps it on only
during a game). A launch_game then starts the game behind the dark lock screen: no surface, the
game draws a few frames and stops ("Failed to initialize Android surface"). Nx2Wake (overlay) wakes
the screen for Lab starts and marks their intents; this script makes EmulationActivity and
MainActivity honour the mark (setShowWhenLocked / setTurnScreenOn / dismiss a swipe lock) and adds
the WAKE_LOCK permission. Starts by the player are unchanged. Run after apply-nx2-claude-bridge.py."""
from pathlib import Path
import sys

main = Path(sys.argv[1]).resolve() / "src/android/app/src/main"
kt = main / "java/io/github/zortos293/lichii"
MARK = "Nx2Wake"


def patch(path, old, new):
    text = path.read_text()
    if MARK in text and old not in text:
        print(f"lab-wake: {path.name} already patched")
        return
    if text.count(old) != 1:
        sys.exit(f"lab-wake: {path.name}: expected 1 match, found {text.count(old)}:\n{old}")
    path.write_text(text.replace(old, new))


emulation = kt / "activities/EmulationActivity.kt"
patch(emulation, """        super.onCreate(savedInstanceState)

        NativeConfig.reloadGlobalConfig()""", """        super.onCreate(savedInstanceState)
        io.github.zortos293.lichii.nx2.Nx2Wake.apply(this, intent)

        NativeConfig.reloadGlobalConfig()""")
patch(emulation, """    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
""", """    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        io.github.zortos293.lichii.nx2.Nx2Wake.apply(this, intent)
""")
patch(kt / "ui/main/MainActivity.kt", """        super.onCreate(savedInstanceState)
        NativeLibrary.initMultiplayer()""", """        super.onCreate(savedInstanceState)
        io.github.zortos293.lichii.nx2.Nx2Wake.apply(this, intent)
        NativeLibrary.initMultiplayer()""")

manifest = main / "AndroidManifest.xml"
text = manifest.read_text()
if "android.permission.WAKE_LOCK" not in text:
    old = '    <uses-permission android:name="android.permission.INTERNET" />\n'
    if text.count(old) != 1:
        sys.exit("lab-wake: INTERNET permission line not found once")
    manifest.write_text(text.replace(
        old, old + '    <uses-permission android:name="android.permission.WAKE_LOCK" />\n'))
print("lab-wake: Lab starts wake the screen and show Eden over the lock screen")
