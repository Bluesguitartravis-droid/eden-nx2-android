# Switch 2 native Android track

This directory is an isolated research track. It does not replace Eden's normal emulator
backend and it does not alter the published Eden APK.

## Goal

Evaluate a second execution path inspired by shirai91/nx2android:

```text
user-owned Switch 2 extracted title
        |
        v
offline ARM64 rewrite / preparation
        |
        v
Android-native host + Switch 2 service compatibility
        |
        v
NVN/Vulkan replay
        |
        v
native Android execution
```

If this becomes viable, Eden can remain the library/settings/launcher shell and choose between:

- Emulated: the existing Eden/SX2A backend.
- Native (experimental): the new per-title prepared runtime for supported Switch 2 titles.

The codebases should remain modular. "Combine into Eden" means exposing the native runtime as
an alternate backend, not copying every nx2android source file into Eden.

## Stage 0 - device/runtime probe

switch2_probe.c builds a game-free NativeActivity APK. It validates:

- an ARM64 Android process;
- a 12 GiB sparse virtual-address reservation;
- RW -> RX executable mappings for rewritten ARM64 code;
- the host counter frequency, while recording the Switch 2 guest target of 31.25 MHz;
- Vulkan availability and BC texture support.

The APK writes switch2-native-probe.txt into its Android external-files directory and also
logs the same lines with tag NX2NativeProbe.

No game files, console firmware, keys, decryption, or copyrighted content are used by this probe.

## Planned milestones

1. Stage 0 - runtime probe.
2. Stage 1 - headless guest bootstrap: accept an already-extracted, user-owned Switch 2
   ExeFS; parse its NPDM, rewrite the minimum ARM64 instructions needed by Android, map
   main, and reach the guest entry/host gate with graphics disabled.
3. Stage 2 - Horizon host compatibility: implement only the services encountered by the
   first test title, borrowing architecture from nx2android and behavior knowledge from
   SX2A/PokeDinner/Eglantine.
4. Stage 3 - graphics: adapt the Android NVN -> Vulkan replay path for Switch 2 behavior.
5. Stage 4 - Eden integration: Eden detects a supported native profile and offers the
   native backend alongside the normal emulator backend.

## Upstream pin

The probe workflow uses nx2android commit:

8e030b41caa05b109d497f486cea5f4abd1c7d73

That pin is intentional so CI is reproducible while the upstream project changes rapidly.
