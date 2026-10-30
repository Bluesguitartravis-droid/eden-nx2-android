# Android port notes

## Porting rule

NX2 support is layered onto Eden's existing Android frontend. Do not remove Android features simply to get NX2 compiling.

Features to preserve include:

- custom Adreno GPU-driver loading (Turnip)
- stock Qualcomm driver selection
- per-game settings
- controller support
- overlay controls
- Android quick settings
- shader-cache management
- Android diagnostic logging

## Compatibility target

This fork is intended to preserve normal Nintendo Switch compatibility from Eden while adding the experimental NX2 / Switch 2 work.

- Existing Switch support is a regression gate: NX2 changes should not break normal Switch titles.
- Switch 2 support is experimental and will be layered into the same Android build.
- Game files, keys, firmware, and other user content are not bundled by this project.

## First milestone

The first milestone is an installable arm64-v8a APK that reaches Eden's normal Android UI on the Odin 3 Ultra, preserves the custom-driver selector, and retains the existing Switch code path.

Runtime NX2 / Switch 2 compatibility comes after the Android build baseline is established.

## Testing

Early builds are experimental. Keep logs when a title fails to boot or render; shader, Vulkan, memory-layout, and OS-service differences are expected areas of investigation.
