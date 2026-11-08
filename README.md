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

Current test builds are built from **Lichii** (zortos293/Lichii `nx2` @ `bf4c42e`, the continuation of
Eglantine NX2) with:
- an ARM64 macro JIT and memory/texture fixes ported from godpow's Eglantine PR #503;
- this repo's Android patches (see [docs/LICHII_BASE.md](docs/LICHII_BASE.md)) and Adreno performance work:
  host-mapped GPU buffers on unified memory, tensor-core (HMMA) emulation through workgroup memory, and NCE
  handling of unmapped memory accesses.

## Game status

Measured on the AYN Odin 3 Ultra (Snapdragon 8 Elite, Adreno 830) with test builds 120-127, NCE CPU backend,
asynchronous GPU emulation on, and the Turnip driver unless noted. Build 131 results are still being collected.

| Game | Status | Performance |
| --- | --- | --- |
| Donkey Kong Bananza | Playable in levels; some fur flicker/stretching | 16-20 FPS in levels (up to ~32 depending on view), 60 FPS title screen. Was ~4 FPS on build 99. |
| Mario Kart World | Races run; roads can turn black and some objects pop in/out; device loss on Qualcomm's stock driver | ~38-46 FPS |
| The Legend of Zelda: Ocarina of Time (.nup) | Reaches the opening video, then stalls compiling shaders; Turnip V34/v37 crash in the shader compiler | Not playable yet |
| Mario Tennis Fever | Playable: a full match plays through; some textures pop in and out | Never below 50 FPS in a full match |
| Fire Emblem: Fortune's Weave | Under test | - |

## Tips

- Launch Switch 2 games as loose folders (or .nup); Switch 2 games inside NSPs are not detected.
- Fully close the app before switching a game's CPU backend (NCE / Dynarmic).
- Test switches for debugging live in `Download/nx2_switches.txt`; leave the file absent for normal play.

## History

- The previous Eglantine base (builds 80-85) is documented in [docs/EGLANTINE_BASE.md](docs/EGLANTINE_BASE.md).
- The previous SX2A v0.3.2 baseline (build 69) is documented in [docs/SX2A_BASE.md](docs/SX2A_BASE.md).
- Original NX2 source: https://git.eden-emu.dev/Takoroka/eden-nx2
