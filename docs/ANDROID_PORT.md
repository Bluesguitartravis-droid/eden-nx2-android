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

## First milestone

The first milestone is an installable arm64-v8a APK that reaches Eden's normal Android UI on the Odin 3 Ultra and preserves the custom-driver selector.

Runtime NX2 compatibility comes after the Android build baseline is established.

## Testing

Early builds are experimental. Keep logs when a title fails to boot or render; shader, Vulkan, memory-layout, and OS-service differences are expected areas of investigation.
