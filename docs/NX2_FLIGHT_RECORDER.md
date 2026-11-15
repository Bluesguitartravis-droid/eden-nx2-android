# NX2 Android Flight Recorder v1

Status: experimental implementation on branch feature/nx2-flight-recorder, based on the
Lichii test branch at Build 150. Not a deterministic replay system.

## Why

The emulator often has no chance to write a final line during a SIGKILL, Android
low-memory kill, GPU device loss or hard crash. Flight Recorder preserves a
bounded timeline *before* the failure and lets Android's next app launch collect
the previous process's exit reason.

The first iteration intentionally does not change NCE execution, graphics
synchronization, Vulkan resource lifetime, or the native signal handler.

## Features

- Every emulation session has a unique ID, a JSON metadata file and a
  monotonic/wall-clock timestamped JSONL event history.
- A background Android memory sample every 10 seconds includes native PSS,
  private-dirty memory, system available memory, Java heap and Android thermal
  status when available. This is not a full GPU memory counter.
- PixelCopy takes one low-resolution (480x270, WebP) **game-surface** image
  every 2.5 seconds when the emulation activity is foregrounded. A ring of 12
  files holds roughly 30 seconds of visual history. A Window capture is used
  if the SurfaceView is not ready. Frames are written off the UI thread.
- Events rotate at 2 MiB, retaining the previous 2 MiB; old session directories
  are pruned. No continuous video encoder, background upload or screen-capture
  permission.
- The in-game drawer has **Save NX2 diagnostic report** and a **screenshots
  On/Off** toggle (enabled by default in experimental builds).
- A report ZIP contains session metadata, JSONL event history, the saved recent
  frames, and at most the final 4 MiB of each available native Eden and
  nx2_crash.txt log. The user chooses the share destination via Android.
- A small on-disk active marker survives a killed process. On next launch,
  ApplicationExitInfo is queried and a process_exit.json is saved to the
  interrupted session. If available, Android's trace stream is saved up to
  4 MiB. The main screen offers to share the recovered report once.

## Export layout

    nx2-flight-<session-id>.zip
      README.txt
      session.json
      events.jsonl
      events.previous.jsonl     [when rotated]
      process_exit.json         [after process restart]
      android_exit_trace.txt   [when Android provides it]
      frames/frame-00.webp ... frame-11.webp
      logs/eden_log.txt         [tail, if present]
      logs/eden_log.txt.old.txt     [tail, if present]
      logs/nx2_crash.txt        [tail, if present]

APK applicationId: dev.eden.eden_emulator. ZIPs are generated in the app's
private cache and shared through the existing Android FileProvider. Session
source files are stored in filesDir/nx2-flight-recorder. Never put game files,
keys, firmware, saved games or arbitrary raw guest-memory dumps in the export.

## How to validate

1. Build the feature branch with GitHub Actions and confirm Kotlin compilation.
2. Launch a game and let it render for more than 30 seconds.
3. From the in-game drawer choose Save NX2 diagnostic report. Examine the ZIP:
   session.json, events.jsonl, <=12 recent images and native log tail.
4. Check the frames show the game, not a blank compositor surface. If PixelCopy
   fails on a driver, capture directly from the renderer as a later fallback.
5. Turn screenshots Off, play 30 seconds, and confirm no new frames are saved.
6. Simulate a **process** kill (not a user-initiated normal exit), relaunch the
   app and verify the recovered-session prompt and process_exit.json.
7. Compare FPS and 1% low frame time versus a build without the recorder in
   the exact same scene to assess sampling overhead.
8. Check ZIP entries never exceed the limits and that existing versions still
   start and stop normally after ROM swaps or Activity recreation.

## Limits and safety

- A screenshot is visual evidence, not an explanation of the Vulkan operation
  that produced it. The existing NX2 native diagnostics remain essential.
- No screenshot can be guaranteed immediately at the instant of an abrupt crash.
- Normal Android memory metrics and OS exit reasons are not enough to recover
  all GPU driver faults, shader-state bugs, or guest synchronization failures.
- A native crash inside the existing C++ signal handler may still prevent it
  from writing nx2_crash.txt: hardening that handler is a separate task.
- The 2.5 second PixelCopy sampling interval is deliberately conservative.
  PixelCopy itself may fail during device loss, surface destruction or low memory.
- The exported log tail may contain game titles, local file paths or other
  user-provided values. **Review the ZIP before sharing it.** Nothing is
  uploaded automatically.
- ApplicationExitInfo availability varies by Android version and the way the
  process died. A recovered report may have no tombstone.
- The current source does not include guest input recording, GPU command replay,
  native event tracing or a live USB/ADB control bridge.

## Recommended v2

1. Async-signal-safe fatal record and tombstone handoff, native structured ring
   buffer with preallocated fixed-size slots and unified session timestamps.
2. Native Vulkan submission/fence/pipeline/texture IDs captured in a bounded
   event history, plus explicit GPU-device-loss snapshots where extensions allow.
3. Controller input timestamps (not arbitrary personal data), visual-anomaly
   markers and on-demand deeper tracing.
4. Opt-in Perfetto capture, optional ADB-assisted export and per-build report
   comparison automation.

<!-- Build 159: first build on Lichii faa4bd13a. -->
