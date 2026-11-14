#!/usr/bin/env python3
"""Install NX2 Flight Recorder v1 into the pinned Lichii Android tree.

The recorder is app-side and does not hook emulator signal handlers or modify
CPU/GPU scheduling. The overlay Kotlin file is copied into the upstream app.
This patch fails closed when upstream UI anchors change.
Usage: python3 scripts/apply-nx2-flight-recorder.py <upstream-root> [build-tag]
"""
from pathlib import Path
import sys

if len(sys.argv) not in (2, 3):
    raise SystemExit("usage: apply-nx2-flight-recorder.py <upstream-root> [build-tag]")
build_tag = sys.argv[2] if len(sys.argv) == 3 else "local"
root = Path(sys.argv[1]).resolve()
repo = Path(__file__).resolve().parent.parent
app = root / "src/android/app/src/main"
kt = app / "java/io/github/zortos293/lichii"
src = repo / "android_overlay/src/main/java/io/github/zortos293/lichii/utils/Nx2FlightRecorder.kt"
dst = kt / "utils/Nx2FlightRecorder.kt"
if not src.is_file() or not dst.parent.is_dir():
    raise SystemExit("Flight recorder overlay or upstream Kotlin directory not found")
dst.write_text(src.read_text().replace("@NX2_BUILD@", build_tag))

def patch(relative, old, new, check):
    path = app / relative
    value = path.read_text()
    if check in value:
        print("already patched:", relative, check)
        return
    if old not in value:
        raise SystemExit(f"Flight recorder anchor missing: {relative}: {old[:105]!r}")
    path.write_text(value.replace(old, new, 1))
    print("patched:", relative)

# Install at process start, before any emulation and without JNI/logging dependencies.
patch("java/io/github/zortos293/lichii/LichiiApplication.kt",
      "        application = this\n",
      "        application = this\n"
      "        io.github.zortos293.lichii.utils.Nx2FlightRecorder.initialize(this)\n",
      "Nx2FlightRecorder.initialize(this)")

activity = "java/io/github/zortos293/lichii/activities/EmulationActivity.kt"
patch(activity,
      "        setContentView(binding.root)\n",
      "        setContentView(binding.root)\n"
      "        io.github.zortos293.lichii.utils.Nx2FlightRecorder.start(this)\n",
      "Nx2FlightRecorder.start(this)")
patch(activity,
      "    override fun onResume() {\n        super.onResume()\n",
      "    override fun onResume() {\n        super.onResume()\n"
      "        io.github.zortos293.lichii.utils.Nx2FlightRecorder.resume(this)\n",
      "Nx2FlightRecorder.resume(this)")
patch(activity,
      "    override fun onPause() {\n",
      "    override fun onPause() {\n"
      "        io.github.zortos293.lichii.utils.Nx2FlightRecorder.pause()\n",
      "Nx2FlightRecorder.pause()")
patch(activity,
      # Anchor on onDestroy: the same removeCallbacks line also opens scheduleRomSwapStopTimeout().
      "        mainHandler.removeCallbacks(romSwapStopTimeoutRunnable)\n        super.onDestroy()\n",
      "        if (isFinishing && !isChangingConfigurations) {\n"
      "            io.github.zortos293.lichii.utils.Nx2FlightRecorder.stop(this)\n"
      "        } else {\n"
      "            io.github.zortos293.lichii.utils.Nx2FlightRecorder.pause()\n"
      "        }\n"
      "        mainHandler.removeCallbacks(romSwapStopTimeoutRunnable)\n        super.onDestroy()\n",
      "Nx2FlightRecorder.stop(this)")

# Offer recovered evidence at the normal launcher, including after a native SIGKILL.
patch("java/io/github/zortos293/lichii/ui/main/MainActivity.kt",
      "        setContentView(binding.root)\n",
      "        setContentView(binding.root)\n"
      "        if (savedInstanceState == null) {\n"
      "            window.decorView.post {\n"
      "                io.github.zortos293.lichii.utils.Nx2FlightRecorder.offerRecovery(this)\n"
      "            }\n"
      "        }\n",
      "Nx2FlightRecorder.offerRecovery(this)")

fragment = "java/io/github/zortos293/lichii/fragments/EmulationFragment.kt"
patch(fragment,
      "    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {\n"
      "        super.onViewCreated(view, savedInstanceState)\n",
      "    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {\n"
      "        super.onViewCreated(view, savedInstanceState)\n"
      "        io.github.zortos293.lichii.utils.Nx2FlightRecorder.attachSurface(binding.surfaceEmulation)\n",
      "Nx2FlightRecorder.attachSurface(binding.surfaceEmulation)")
patch(fragment,
      "        updateGameTitle()\n\n"
      "        binding.inGameMenu.menu.findItem(R.id.menu_quick_settings)",
      "        updateGameTitle()\n"
      "        binding.inGameMenu.menu.findItem(R.id.menu_nx2_flight_recovered)?.isVisible =\n"
      "            io.github.zortos293.lichii.utils.Nx2FlightRecorder.hasRecoveredReport()\n"
      "        binding.inGameMenu.menu.findItem(R.id.menu_nx2_flight_capture)?.title =\n"
      "            getString(if (io.github.zortos293.lichii.utils.Nx2FlightRecorder.screenshotsEnabled(requireContext()))\n"
      "                R.string.nx2_flight_capture_on else R.string.nx2_flight_capture_off)\n\n"
      "        binding.inGameMenu.menu.findItem(R.id.menu_quick_settings)",
      "R.id.menu_nx2_flight_capture)?.title")
patch(fragment,
      "            when (it.itemId) {\n"
      "                R.id.menu_pause_emulation -> {",
      "            when (it.itemId) {\n"
      "                R.id.menu_nx2_flight_recovered -> {\n"
      "                    binding.drawerLayout.closeDrawer(binding.inGameMenu)\n"
      "                    io.github.zortos293.lichii.utils.Nx2FlightRecorder.shareReport(requireActivity(), true)\n"
      "                    true\n"
      "                }\n"
      "                R.id.menu_nx2_flight_report -> {\n"
      "                    binding.drawerLayout.closeDrawer(binding.inGameMenu)\n"
      "                    io.github.zortos293.lichii.utils.Nx2FlightRecorder.shareReport(requireActivity())\n"
      "                    true\n"
      "                }\n"
      "                R.id.menu_nx2_flight_capture -> {\n"
      "                    val enabled = io.github.zortos293.lichii.utils.Nx2FlightRecorder.toggleScreenshots(requireContext())\n"
      "                    it.title = getString(if (enabled) R.string.nx2_flight_capture_on\n"
      "                        else R.string.nx2_flight_capture_off)\n"
      "                    true\n"
      "                }\n"
      "                R.id.menu_pause_emulation -> {",
      "R.id.menu_nx2_flight_report ->")

# Drawer entries use existing icons; no new resources or permissions.
patch("res/menu/menu_in_game.xml",
      "    <item\n"
      "        android:id=\"@+id/menu_exit\"",
      "    <item\n"
      "        android:id=\"@+id/menu_nx2_flight_recovered\"\n"
      "        android:icon=\"@drawable/ic_settings_outline\"\n"
      "        android:title=\"@string/nx2_flight_recovered\" />\n\n"
      "    <item\n"
      "        android:id=\"@+id/menu_nx2_flight_report\"\n"
      "        android:icon=\"@drawable/ic_settings_outline\"\n"
      "        android:title=\"@string/nx2_flight_report\" />\n\n"
      "    <item\n"
      "        android:id=\"@+id/menu_nx2_flight_capture\"\n"
      "        android:icon=\"@drawable/ic_settings_outline\"\n"
      "        android:title=\"@string/nx2_flight_capture_on\" />\n\n"
      "    <item\n"
      "        android:id=\"@+id/menu_exit\"",
      "menu_nx2_flight_report")
patch("res/values/strings.xml",
      "</resources>",
      "    <string name=\"nx2_flight_recovered\">Share last interrupted report</string>\n"
      "    <string name=\"nx2_flight_report\">Save NX2 diagnostic report</string>\n"
      "    <string name=\"nx2_flight_capture_on\">Flight recorder screenshots: On</string>\n"
      "    <string name=\"nx2_flight_capture_off\">Flight recorder screenshots: Off</string>\n"
      "</resources>",
      "name=\"nx2_flight_report\"")

# Verify the source was copied and user-visible menu ID references were inserted.
assert dst.is_file() and "object Nx2FlightRecorder" in dst.read_text()
assert "menu_nx2_flight_report" in (app / fragment).read_text()
assert "menu_nx2_flight_report" in (app / "res/menu/menu_in_game.xml").read_text()
print("NX2 Flight Recorder v1 installed: rolling frames, events, recovery, ZIP sharing")
