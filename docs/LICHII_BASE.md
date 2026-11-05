# Lichii base (main from build 98)

`main` builds zortos293/Lichii `nx2` at `27226d2` (176 commits after its build-75 tag), the continuation of
Eglantine NX2 with Bananza, Ocarina of Time and GPU-readback fixes. The workflow clones it, applies the
scripts below, and builds the arm64 APK.

## Android patches (scripts/)

| Script | Why |
|---|---|
| `configure-lichii.py` | Package ID `dev.eden.eden_emulator`, build tag `build-N`, and an `android-arm64` updater platform that maps to this repo's `Eden-NX2-Android-buildN-standard.apk` release assets |
| `apply-eglantine-switch2-regressions-tolerant.py` | Loose ExeFS discovery and other Switch 2 Android fixes; the old "force Dynarmic for DKB" diagnostic is disabled |
| `apply-nx2-direct-exefs.py`, `apply-nx2-content-guards.py` | Launch ExeFS folders directly; prefixed-ZBIC NSO frames and RomFS diagnostics |
| `apply-android-map-budget.py`, `apply-android-heap-tracker-budget.py` | Stay under Android's 65,530 memory-mapping limit; `NX2_MAPS` / `NX2_HEAP` logging |
| `apply-android-atomic-ref-compat.py`, `apply-android-int64-compat.py` | NDK libc++ gaps (atomic_ref; long long vs s64 in std::max) |
| `apply-android-mimalloc.py` | operator new/delete from mimalloc (local-dynamic TLS). Fixes the DKB "Scudo internal map failure" crash: scudo's 256 MiB size-class regions filled and its fallback used 2-3 mappings per allocation |
| `apply-nx2-fault-report.py` | Host crash location (`NX2_FAULT`) in the log, logcat and `nx2_crash.txt` |
| `apply-nx2-hostmem-diag.py` | `NX2_HOSTMEM`: why fastmem mappings fail |
| `apply-nx2-hang-watchdog.py`, `apply-nx2-hang-sample.py` | `NX2_HANG`: guest thread dump with live PCs and backtraces after 8 s without a frame |
| `apply-nx2-gpu-profile-on.py` | GPU-thread profiler; test branches only, not on `main` |

## Known problems

- DKB and Ocarina of Time hang on NCE: the main thread spins in the SDK graphics driver
  (NvOsGetTimeUS / TickManager::ConvertToTimeSpan) waiting for a value the emulator does not provide.
  Mario Tennis Fever runs on NCE.
- DKB gameplay is bound by GPU round trips for GPU-written command lists (~75 full GPU waits per second).
- Switching a game's CPU backend without closing the app leaves fastmem in the previous layout.
