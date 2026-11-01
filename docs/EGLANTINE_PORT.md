# Eglantine NX2 Android port notes

This project selectively ports fixes from the public Eglantine NX2 work rather than replacing the known-good Eden NX2 Android baseline wholesale.

## First portable-fix batch

Source repository: https://github.com/zortos293/Eglantine

Pinned commits:

- `a4a746b47b1d1ee02ddf1fe528c7db05800eec2c` - Ampere RET/R0 handling; reported to restore Mario Kart World text.
- `d2ac2275247a9ecb3e1404606f7026c6402533b8` - operation-mode refresh for Switch 2 docked/handheld changes, including Android Quick Settings.
- `ca524c9aa883abc4d1c5aeb8aa4d2f9eb0b4225c` - prevent invalid Vulkan 2D/3D image-view mismatches.
- `ea33ad1865970ee731e5f74dbcc26ca472798afe` - atomic Vulkan pipeline-cache writes and truncated-cache recovery.
- `ddd367a8152f1c37bfc972475b40afece2da31ac` - end stale device-memory page runs when mappings change.
- `59047f0e6fd149e8187092ec0d9f419aa03e875f` - run destructors for pooled shader objects.

These are applied at build time by `scripts/apply-eglantine-fixes.sh`.

The larger Eglantine read-trap, sparse-memory, GPU readback and automatic ASTC-recompression work is intentionally deferred to a separate batch because it touches many memory/cache paths and should be regression-tested independently on Android.

## In-app APK updates

Eden already contains Android APK download/install UI. This fork reuses it and changes the release feed to this repository's GitHub Releases.

Each successful main build is published as tag `build-N`, and the APK asset name ends in `standard.apk` so Eden's Android platform-asset selector recognizes it.

Android does not permit a normal app to silently replace itself. The app can download the APK and launch the package installer, but the user must approve the Android install/update prompt.
