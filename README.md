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

- **Test builds** (`lichii-test-N` pre-releases, from the `claude/lichii-test` branch) are the newest and fastest.
  They are not marked "latest", so the in-app updater does not offer them; download the APK from the
  [releases page](https://github.com/Bluesguitartravis-droid/eden-nx2-android/releases).
- **`main`** (release `build-99`) is older and much slower; every successful build on `main` is published as a
  release, and the in-app updater follows those.

Current test builds are built from **Lichii** (zortos293/Lichii `nx2` @ `cb5c5c7`, the continuation of
Eglantine NX2) with:
- an ARM64 macro JIT ported from godpow's Eglantine PR #503;
- this repo's Android patches (see [docs/LICHII_BASE.md](docs/LICHII_BASE.md)) and Adreno performance work:
  host-mapped GPU buffers on unified memory, tensor-core (HMMA) emulation through workgroup memory with
  unrolled loops, NCE handling of unmapped memory accesses, and reduced-size terrain texture arrays;
- extra logging: every 10 s the log records frame rate and frame-time percentiles (`NX2_FRAMES`), GPU load
  (`NX2_GPU`) and thread CPU time, and at exit a one-line summary (`NX2_SUMMARY`) and who stopped the game
  (`NX2_STOP`).

## Game status

Measured on the AYN Odin 3 Ultra (Snapdragon 8 Elite, Adreno 830, 24 GB) with test builds 147-149 and a Turnip
driver. Figures come from the logs of single play sessions, so treat them as indicative.

| Game | Status (builds 147-149) | Performance |
| --- | --- | --- |
| Donkey Kong Bananza | Playable in levels; some fur flicker/stretching | 15-20 FPS in busy areas, 25-29 in lighter ones (NCE); 17-20 on Dynarmic. 60 FPS title screen. Was ~4 FPS on build 99. |
| Mario Kart World | Races run and the ground is textured since build 149 (it was black before). Scenery sometimes turns into large stripes; the Turnip driver reported a device loss after about 5 minutes in one session | 18-20 FPS in a race, 60 in menus (NCE) |
| The Legend of Zelda: Ocarina of Time (.nup) | Opening video and menus run; Link is controllable. With an empty shader cache the game quits (error 2422-6272) for the first few launches until the cache has filled; changing the Turnip version empties it again | 60 FPS video and menus; 13-20 FPS in gameplay depending on the Turnip version (Dynarmic) |
| Mario Tennis Fever | Playable: a full match plays through; some textures pop in and out | Never below 50 FPS in a full match (builds 120-127; not re-measured) |
| The Legend of Zelda: Tears of the Kingdom (Switch 1) | Playable | Locked 30 FPS (build 145) |
| Star Fox | Gets in-game; dialogue stutters while shaders build for the first time; some texture flashing | 30 FPS between shader stalls |
| Yoshi and the Mysterious Book | Under test: black gameplay with sound on earlier drivers (unsupported shader instructions); a device loss after 3 minutes on a newer Turnip | - |
| Fire Emblem: Fortune's Weave | Under test | - |

Known open problems: Switch 2 mesh-shader draws are skipped (missing scenery), the GameChat and camera
services are placeholders, and frame generation has not been tested here.

## Tips

- Launch Switch 2 games as loose folders (or .nup); Switch 2 games inside NSPs are not detected. Folder games
  can be started from the game list since build 145.
- After changing the Turnip driver version, a game may need a launch or two to rebuild its shader cache.
- Fully close the app before switching a game's CPU backend (NCE / Dynarmic).
- Test switches for debugging live in `Download/nx2_switches.txt`; leave the file absent for normal play.

## History

- The previous Eglantine base (builds 80-85) is documented in [docs/EGLANTINE_BASE.md](docs/EGLANTINE_BASE.md).
- The previous SX2A v0.3.2 baseline (build 69) is documented in [docs/SX2A_BASE.md](docs/SX2A_BASE.md).
- Original NX2 source: https://git.eden-emu.dev/Takoroka/eden-nx2
