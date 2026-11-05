# Eden NX2 Android

Experimental Android/ARM64 build of the Eden NX2 fork, targeting devices such as the AYN Odin 3 Ultra. The goal is to retain Eden's existing Nintendo Switch support while adding the NX2 / Switch 2 work in the same APK.

## Goals

- Preserve Eden's Android frontend and Android-specific features.
- Preserve custom Adreno GPU driver loading, including Turnip support where supported by Eden.
- Integrate the NX2 core changes from Takoroka/eden-nx2.
- Produce installable arm64-v8a APK artifacts through GitHub Actions.
- Keep diagnostic logging enabled for early Android/Odin testing.

## Upstream

NX2 source: https://git.eden-emu.dev/Takoroka/eden-nx2

This repository is an experimental community build project. It is not an official Eden release.

## Build status

`main` builds from **Lichii** (zortos293/Lichii `nx2`, commit `27226d2`), the continuation of Eglantine NX2,
with the Android patches described in [docs/LICHII_BASE.md](docs/LICHII_BASE.md). Every successful build on
`main` is published as a GitHub release; the in-app updater follows those releases.

On the AYN Odin 3 Ultra (Adreno 830, Turnip):
- **Mario Tennis Fever** runs to its title screen at about 50 FPS (first seen on build 83, also on NCE here).
- **Donkey Kong Bananza** no longer crashes and shows graphics in gameplay, but is a slideshow (about 4-8 FPS):
  set its CPU backend to **Dynarmic**, since it hangs on NCE.
- **The Legend of Zelda: Ocarina of Time** (.nup) reaches its title screen on Dynarmic; after pressing start
  the screen stays black.

Launch Switch 2 games as loose folders (or .nup), and fully close the app before switching a game's CPU backend.

The previous Eglantine base (builds 80-85) is documented in [docs/EGLANTINE_BASE.md](docs/EGLANTINE_BASE.md).
The previous SX2A v0.3.2 baseline (build 69) is documented in [docs/SX2A_BASE.md](docs/SX2A_BASE.md).
