# SX2A / PokeDinner Android base

This branch pivots the Switch 2 layer from Takoroka's older `eden-nx2`
baseline to moceure's Android-focused NX2 work built on PokeDinner.

## Upstream pin

- Repository: `https://git.eden-emu.dev/moceure/eden-nx2-android.git`
- Branch: `nx2-android`
- Pinned commit: `904bfa266c23814660336db6bb0e87caf1c15305`
- Lineage: PokeDinner / Eglantine / Eden-NS2 / Takoroka / Eden

The exact commit is pinned so an APK can be reproduced even when upstream moves.

On 2026-11-02 upstream rewrote its history (every commit ID changed) and marked the
project unmaintained. The old pin `ee1e9a0e408f2725baf15f7ca2246e746f31b849` no longer
exists; the `nx2-android-v0.3.1` tag now points to `904bfa266c23814660336db6bb0e87caf1c15305`.
Upstream also published `nx2-android-v0.3.2` (`dd3cb327c79f511830e460b916a45a33b5738759`).

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
