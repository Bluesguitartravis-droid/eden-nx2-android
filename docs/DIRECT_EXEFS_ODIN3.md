# Build 64: direct loose ExeFS on Odin 3 Ultra

Our previous Build 63 Android app could discover loose ExeFS under registered
game folders, but the games-screen folder button still opened only the standard
library-directory picker. That did not offer a direct `exefs/main` launch.

## What changed

- The games-screen folder button opens a two-choice menu:
  `Add games folder` or `Launch ExeFS game folder`.
- Direct ExeFS launch accepts the game parent or `exefs/` itself, detects
  `main`, and presents a chooser if multiple candidates (base and updates)
  are found. Real-path access is checked; the app offers the Android **All
  files access** settings page when permission is missing.
- Native loading from `updates/<version>/exefs/main` now searches for the
  same game's `base/romfs` and builds a merged RomFS in memory. It validates
  matching `main.npdm` title IDs first, preserves files existing only in the
  base, and lets update files override names in their directories.
- The console records `NX2_LOOSE_ROMFS mode=base+update` or
  `mode=single`, plus `NX2_ROMFS_PROBE` with the content it actually mounted.
- NSP and NUP paths remain available, as do all Build 63 diagnostics.

## Recommended test on the Odin 3 Ultra

In Eden NX2, open the games screen and tap the folder button. Choose
**Launch ExeFS game folder**. Then select the parent containing one of these
layouts:

```text
MyGame/
  exefs/main
  exefs/main.npdm
  exefs/rtld
  exefs/sdk
  romfs/...
```

or:

```text
MyGame/
  base/exefs/main
  base/exefs/main.npdm
  base/romfs/...
  updates/458752/exefs/main
  updates/458752/exefs/main.npdm
  updates/458752/romfs/...
```

When presented with multiple versions, select the highest update version
that matches the original game. The direct launch avoids our custom NSP
packer entirely. The `base + update` merge only works with both extracted
trees already present; it does not create missing content, bypass encryption,
or implement specialized deletion semantics.

## What to look for in logs

- `NX2_LOOSE_ROMFS mode=base+update`: correct base/update folder pair found.
- `NX2_ROMFS_PROBE DKB ... System/HeapSize=true RSDB=true`: the specific
  resource directories investigated in Eglantine issue #335 are mounted.
- `NX2_DKB_FIRST_FAULT`: first real guest invalid memory access when it still
  fails; inspect PC/LR without a 40,000-line log storm.

A successfully compiled APK is not a claim that Mario Kart World, DKB, or
other Switch 2 titles will run at playable speed. The Odin 3 Ultra still
needs on-device validation.
