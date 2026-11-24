#!/usr/bin/env python3
"""Claude Lab: report EmulationActivity's lifecycle, and log why it leaves the front.

Games started by the Lab were sometimes paused ~30 ms after resuming and never resumed: the game
stopped drawing ("Eden is in the background"). The log said nothing about what came on top. Now
EmulationActivity logs start/stop, window focus, top-resumed changes, picture-in-picture changes
(it auto-enters PiP when the setting is on) and the user-leave hint, with the PiP state, window
focus and Eden's own top activities at each pause; and it tells Nx2Bridge whether it is resumed, so
the Lab can bring it back to the front after a launch (Nx2Tools.keepGameInFront) and status shows
the last events (game_screen=...). Run after apply-nx2-diagnostics.py (its [NX2_LIFECYCLE] lines)."""
from pathlib import Path
import sys

act = (Path(sys.argv[1]).resolve() /
       "src/android/app/src/main/java/io/github/zortos293/lichii/activities/EmulationActivity.kt")
text = act.read_text()
MARK = "nx2LifecycleState"
already = MARK in text

BRIDGE = "io.github.zortos293.lichii.nx2.Nx2Bridge"


def rep(old, new):
    global text
    if already:
        return
    if text.count(old) != 1:
        sys.exit(f"lab-lifecycle: expected 1 match, found {text.count(old)}:\n{old}")
    text = text.replace(old, new)


rep('        Log.info("[NX2_LIFECYCLE] EmulationActivity resumed")\n',
    '        Log.info("[NX2_LIFECYCLE] EmulationActivity resumed")\n'
    f'        {BRIDGE}.noteGameScreen("resumed", true)\n')
rep('        Log.info("[NX2_LIFECYCLE] EmulationActivity paused (finishing=$isFinishing)")\n',
    '        Log.info("[NX2_LIFECYCLE] EmulationActivity paused (finishing=$isFinishing) ${nx2LifecycleState()}")\n'
    f'        {BRIDGE}.noteGameScreen("paused ${{nx2LifecycleState()}}", false)\n')
rep("    override fun onUserLeaveHint() {\n", f"""    // NX2 Claude Lab (scripts/apply-nx2-lab-lifecycle.py)
    private fun nx2LifecycleState(): String = runCatching {{
        val tasks = getSystemService(android.app.ActivityManager::class.java)?.appTasks.orEmpty()
            .joinToString(",") {{ task ->
                (task.taskInfo.topActivity?.shortClassName ?: "?") + "/" + task.taskInfo.numActivities
            }}
        "pip=$isInPictureInPictureMode focus=${{hasWindowFocus()}} tasks=[$tasks]"
    }}.getOrDefault("?")

    override fun onStart() {{
        super.onStart()
        Log.info("[NX2_LIFECYCLE] EmulationActivity started")
        {BRIDGE}.noteGameScreen("started")
    }}

    override fun onStop() {{
        Log.info("[NX2_LIFECYCLE] EmulationActivity stopped ${{nx2LifecycleState()}}")
        {BRIDGE}.noteGameScreen("stopped ${{nx2LifecycleState()}}", false)
        super.onStop()
    }}

    override fun onWindowFocusChanged(hasFocus: Boolean) {{
        super.onWindowFocusChanged(hasFocus)
        Log.info("[NX2_LIFECYCLE] EmulationActivity window focus $hasFocus")
        {BRIDGE}.noteGameScreen("focus $hasFocus")
    }}

    override fun onTopResumedActivityChanged(isTopResumedActivity: Boolean) {{
        super.onTopResumedActivityChanged(isTopResumedActivity)
        Log.info("[NX2_LIFECYCLE] EmulationActivity top resumed $isTopResumedActivity")
        {BRIDGE}.noteGameScreen("top $isTopResumedActivity")
    }}

    override fun onUserLeaveHint() {{
        Log.info("[NX2_LIFECYCLE] EmulationActivity user leave hint ${{nx2LifecycleState()}}")
        {BRIDGE}.noteGameScreen("user leave hint")
""")
rep("""        super.onPictureInPictureModeChanged(isInPictureInPictureMode, newConfig)
""", f"""        super.onPictureInPictureModeChanged(isInPictureInPictureMode, newConfig)
        Log.info("[NX2_LIFECYCLE] EmulationActivity picture-in-picture $isInPictureInPictureMode")
        {BRIDGE}.noteGameScreen("pip $isInPictureInPictureMode")
""")
act.write_text(text)

# Usage access (granted by the player in Settings) lets the Lab name the app that comes to the
# front over the game (Nx2Bridge.recentForegroundApps).
manifest = act.parents[6] / "AndroidManifest.xml"
mt = manifest.read_text()
if "android.permission.PACKAGE_USAGE_STATS" not in mt:
    anchor = '    <uses-permission android:name="android.permission.INTERNET" />\n'
    if mt.count(anchor) != 1:
        sys.exit("lab-lifecycle: INTERNET permission line not found once")
    perm = ('    <uses-permission android:name="android.permission.PACKAGE_USAGE_STATS"\n'
            '        tools:ignore="ProtectedPermissions" />\n')
    mt = mt.replace(anchor, anchor + perm)
    if 'xmlns:tools=' not in mt:
        mt = mt.replace('<manifest ', '<manifest xmlns:tools="http://schemas.android.com/tools" ', 1)
    manifest.write_text(mt)
print("lab-lifecycle: EmulationActivity reports its lifecycle to the Lab")
