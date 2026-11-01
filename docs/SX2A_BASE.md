# SX2A / PokeDinner Android base

This branch pivots the Switch 2 layer from Takoroka's older `eden-nx2`
baseline to moceure's Android-focused NX2 work built on PokeDinner.

## Upstream pin

- Repository: `https://git.eden-emu.dev/moceure/eden-nx2-android.git`
- Branch: `nx2-android`
- Pinned commit: `ee1e9a0e408f2725baf15f7ca2246e746f31b849`
- Lineage: PokeDinner / Eglantine / Eden-NS2 / Takoroka / Eden

The exact commit is pinned so an APK can be reproduced even when upstream moves.

## What we keep from this repository

- GitHub Actions APK builds
- GitHub Release publishing
- In-app update feed pointed at `Bluesguitartravis-droid/eden-nx2-android`
- Package ID `dev.eden.eden_emulator` so it updates the existing build
- Existing Switch support as a regression requirement
- Eden custom GPU-driver / Turnip support

## Regression gate

Before replacing the known-good build #15:

1. APK installs over build #15.
2. App starts and preserves existing settings.
3. Custom/Turnip driver selection still works.
4. A normal Switch title such as Tears of the Kingdom still boots and plays.
5. Switch 2 readiness screen and loose ExeFS discovery are present.

No games, keys, firmware, or proprietary assets are included.
