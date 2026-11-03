# NX2 Android: RomFS completeness and NSO decoding triage

Source branch: `fix/nx2-romfs-completeness-and-nso`. Planned first Android CI run: Build 60.

## Observed, not inferred

Build 57 on AYN Odin 3:

- `IsSwitch2Program` detected embedded NSP Program-NCA NPDM before emulation.
- Correct NX2 memory layout (12 GiB), counter (31.25 MHz) and pointer buffer (`0x10000`).
- `OpenSaveDataFileSystem` returned a real directory and `IFileSystem`; it was not a failed save-data response.
- DKB (`040074A01BF12000`, `main=A1DD7480...`, `sdk=B7FFF614...`) then failed under NCE around `SilverStar.nss+0x5AF1C8`, with many invalidation failures at low addresses.
- The other tested title (`0400A3A01EE4C000`) logged repeated `ASSERT(r == int(nso_header.segments[i].size))` while LZ4-decoding its NSO modules, but the loader proceeded anyway.

## Strong external comparison, still a hypothesis for this APK

[Eglantine issue #335](https://github.com/Djihads80/Eglantine/issues/335) isolated the same class of DKB null-pointer initialization failure when the effective RomFS lacked update content: `System/HeapSize` and `RSDB`. Moving the update's extracted RomFS files into the mounted tree changed the failure. This is not proof that the Android `base.nsp` is missing those files; the new runtime probe must check it.

The existing Android snapshot packer selected any structurally complete snapshot with a **nonempty** `romfs/`, including base-only content that can omit the files this game requires. The packer now rejects such DKB snapshots before producing another misleadingly bootable NSP. If a source contains multiple complete snapshots, it still chooses the highest detected numeric version; **the packer does not promise a bit-exact base+update overlay or reconstruct deleted-file semantics**. Use a complete already-extracted/merged snapshot for compatibility experiments.

## New marker: effective RomFS

`RomFSFactory::OpenCurrentProcess` prints one warning per factory instance for the DKB title:

```
NX2_ROMFS_PROBE DKB base_bytes=... mounted_bytes=... romfs_valid=...
  System=... System/HeapSize=... RSDB=...
```

This probes the **returned patched RomFS image**, not simply the source NSP name or a `PatchRomFS` attempt.

- `System/HeapSize=false` or `RSDB=false`: verify the extracted snapshot and its update. Do not attribute the subsequent null-pointer crash to save data, Vulkan, or the CPU backend.
- Both true: the missing-file hypothesis is not established. Continue at `main+0x5AF1C8` using guest instruction/return-address tracing and compare Dynarmic against NCE.
- `romfs_valid=false`: the current process has no readable RomFS image in the expected format. Diagnose packaging or mount/patch order first.

The probe reads RomFS *metadata*, not file contents. It runs for DKB only and logs once per game filesystem factory.

## New marker: guarded NSO modules

Compressed sections now check exact input read and decompressed byte counts; uncompressed sections use their true size instead of the compressed-size field. A bounded ZBIC frame-header sniff and +0x100/+0x108 alternate text offset are attempted before rejecting a section.

```
NSO ... segment ... decompression failed: got=... expected=...
```

An unsuccessful section now stops module loading instead of copying potentially invalid decoded data into executable memory. The new decoder is source-level functionality; until it runs on the affected snapshot, do **not** claim that it resolved that title's compressed NSOs.

## Validation gates

1. `python3 -m py_compile` on both patch scripts in CI.
2. Apply the guard to the pinned Eglantine source; assert each marker appears.
3. Kotlin compile preflight and native C++ build on GitHub Actions.
4. On-device: validate launch with both graphics drivers if possible, then inspect `NX2_ROMFS_PROBE`, `NSO ... segment`, and the first true guest error. No claim of a visible frame without a recorded present.
5. Normal Switch titles must still load; the snapshot check is title-ID specific and the RomFS inspection is diagnostic-only.
