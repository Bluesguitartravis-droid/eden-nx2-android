# Eden NX2 Android

Experimental Android/ARM64 build of the Eden NX2 fork, targeting devices such as the AYN Odin 3 Ultra. The goal is to retain Eden's existing Nintendo Switch support while adding the NX2 / Switch 2 work in the same APK.

This repository is an experimental community build project. It is not an official Eden release.

## Goals

- Preserve Eden's Android frontend and Android-specific features.
- Preserve custom Adreno GPU driver loading, including Turnip support where supported by Eden.
- Integrate the NX2 / Switch 2 core work (now carried by Lichii, see below).
- Produce installable arm64-v8a APK artifacts through GitHub Actions.
- Keep diagnostic logging enabled for early Android/Odin testing.

## Which build to use

- **`main`** (releases `build-N`, marked "latest", followed by the in-app updater) is the recommended build.
  From build 178 it carries the same code as the test builds, without the test-only profilers.
- **Test builds** (`lichii-test-N` pre-releases, from the `claude/lichii-test` branch) get new changes first.
  They are not marked "latest"; download the APK from the
  [releases page](https://github.com/Bluesguitartravis-droid/eden-nx2-android/releases).

Both are built from **Lichii** (zortos293/Lichii `nx2` @ `dc9af20`, 2026-11-14, the continuation of Eglantine
NX2) with:
- an ARM64 macro JIT ported from godpow's Eglantine PR #503;
- this repo's Android patches (see [docs/LICHII_BASE.md](docs/LICHII_BASE.md)) and Adreno performance work:
  host-mapped GPU buffers on unified memory, tensor-core (HMMA) emulation through workgroup memory with
  unrolled loops, NCE handling of unmapped memory accesses, reduced-size terrain texture arrays, pointer-load
  shaders that read their buffer addresses once per invocation (about 5 ms less GPU time per frame in Ocarina
  of Time), per-primitive outputs in mesh-shader emulation, and frame-threaded movie decoding;
- **Precache shaders (Switch 2 games)** and **Build all cached shaders** in Settings > Graphics (also per game):
  the precache takes every compute shader out of a Switch 2 game's files and builds it on the loading screen
  (a few minutes the first time, and after a driver change); the loading screen shows the step, a count and
  the time left;
- a flight recorder (Save report in the in-game menu): logs, memory and thermal samples and screenshots in one
  ZIP, and a dialog when a game itself aborts;
- extra logging: every 10 s the log records frame rate and frame-time percentiles (`NX2_FRAMES`), GPU load
  (`NX2_GPU`), CPU clocks (`NX2_CPU`), memory (`NX2_PROC`) and thermal state, and at exit a one-line summary
  (`NX2_SUMMARY`).

## Game status

Measured on the AYN Odin 3 Ultra (Snapdragon 8 Elite, Adreno 830, 24 GB) with a Turnip driver (Gen 8 V34/V39).
Figures come from the logs of single play sessions, so treat them as indicative.

| Game | Status | Performance |
| --- | --- | --- |
| Donkey Kong Bananza | Playable in levels; some fur flicker/stretching | 15-20 FPS in busy areas, 25-29 in lighter ones (NCE); 17-20 on Dynarmic. 60 FPS title screen. Was ~4 FPS on build 99. |
| Mario Kart World | Races run; mesh-shader scenery is drawn since build 162. Large flat stripes appear on every Turnip version tested (not on Qualcomm's driver, which has black ground instead) | ~20 FPS in a race, 60 in the fly-over and menus |
| The Legend of Zelda: Ocarina of Time (.nup) | Playable on Dynarmic (NCE hangs while loading); smooth title video since build 161; Link's face renders correctly since build 170. Long sessions can reach 18-19 GB and be closed by Android; one start aborted with 2466-3264 | 60 FPS video and menus; 15-21 FPS in Kokiri Forest (GPU-bound, ~44 ms of GPU time per frame) |
| Mario Tennis Fever | Playable: a full match plays through; some textures pop in and out | Never below 50 FPS in a full match (builds 120-127; not re-measured) |
| The Legend of Zelda: Tears of the Kingdom (Switch 1) | Playable | Locked 30 FPS (build 145) |
| Star Fox | Gets in-game; dialogue stutters while shaders build for the first time; some texture flashing | 30 FPS between shader stalls |
| Yoshi and the Mysterious Book | The 3D world renders since build 160 (Lichii base) | - |
| Fire Emblem: Fortune's Weave | Under test | - |

Known open problems: Mario Kart World's stripes on Turnip, Ocarina of Time's memory use and NCE hang, a few
very large compute shaders that take 13-17 ms of a frame in Ocarina of Time, the GameChat and camera services
are placeholders, and frame generation has not been tested here.

## Tips

- Launch Switch 2 games as loose folders (or .nup); Switch 2 games inside NSPs are not detected. Folder games
  can be started from the game list since build 145.
- After changing the Turnip driver version, a game rebuilds its shaders. For Switch 2 games, turn on
  Precache shaders (per game) to do that on the loading screen instead of during play.
- Settings > Graphics > Hacks > Pipeline Worker Threads (default 2) speeds up shader building; some Turnip
  versions lose the device with many threads, so go back to 2 if that happens.
- Fully close the app before switching a game's CPU backend (NCE / Dynarmic).
- Test switches for debugging live in `Download/nx2_switches.txt`; leave the file absent for normal play.

## History

- The previous Eglantine base (builds 80-85) is documented in [docs/EGLANTINE_BASE.md](docs/EGLANTINE_BASE.md).
- The previous SX2A v0.3.2 baseline (build 69) is documented in [docs/SX2A_BASE.md](docs/SX2A_BASE.md).
- Original NX2 source: https://git.eden-emu.dev/Takoroka/eden-nx2
