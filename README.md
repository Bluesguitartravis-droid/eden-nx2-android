# Eden NX2 Android

Experimental Android/ARM64 build of the Eden NX2 fork, targeting devices such as the AYN Odin 3 Ultra.

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

The initial CI workflow intentionally builds directly from the upstream NX2 source so that we can establish an Android compile baseline before carrying patches locally.
