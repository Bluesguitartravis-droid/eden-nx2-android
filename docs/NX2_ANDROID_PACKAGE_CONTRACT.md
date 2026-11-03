# NX2 Android launch formats: code audit and packaging contract

## Answer to "which file type is required?"

**No single extension is required**. The pinned Eglantine native loader
(`zortos293/Eglantine` `3bdd53d08adfd96b7e3e3f09344c514540ef1ce6`) recognizes
NSP, XCI, NUP, deconstructed `main`/ExeFS and other formats. Android's game list
recognizes NSP/XCI and the NX2 harness adds NUP and loose ExeFS directory scanning.

**Recognized is not equivalent to fully runnable.** `GetLoader` can identify
a PFS0/NSP and load its Program NCA, yet guest initialization can still fail
if RomFS content is missing, outdated or not visible in the running process.

## Actual native requirements

- **NSP:** a recognizable PFS0 package containing a usable Program NCA with
  ExeFS, including `main`, `main.npdm`, required `rtld` and SDK modules,
  and readable RomFS. Control/metadata contents support game listing and
  version metadata. The Android snapshot packer creates a plaintext,
  unsigned, emulator-specific NSP, not a retail-authenticated cartridge file.
- **NUP:** a native NX Package container (`NUP\x01`) holding one base and
  optional versioned updates. `AppLoader_NUP::ReadRomFS` explicitly calls
  `NupContainer::MergeShadowDirs(base, update)`. A valid standalone update
  can contain a complete patched tree. The Android native loader supports
  it; current mobile packer writes NSP, not NUP.
- **Loose ExeFS:** a `main` NSO beside `main.npdm` and the other modules,
  with a `romfs/` sibling under the title folder or a supported RomFS file.
  The game's `main` must be loaded using the directory loader, not
  treated as a standalone NSO that silently skips SDK/NPDM.
- **XCI:** alternative container, not mandatory. Moving our generated NSP
  to XCI would not supply missing game resources.

## Evidence already collected on Android

Build 57 recognized the generated NSP's embedded Switch 2 NPDM, switched
to the NX2 12-GiB layout and 31.25-MHz counter, selected the 0x10000 pointer
buffer, loaded NSO modules and initialized Vulkan. These checks establish
the container's minimum loadability on this emulator and rule out a
*mandatory XCI/NUP extension* as the immediate blocker.

They do **not** establish whether the NSP's mounted RomFS contains every
resource required by DKB.

Independent comparison: [Eglantine #335](https://github.com/Djihads80/Eglantine/issues/335)
reports a similar startup failure when DKB cannot access the updated
`System/HeapSize` and `RSDB`. Restoring update resources changes the
failure. This is a **lead, not confirmation**, for the Android image.

## Android packer compatibility gap and new fix

Before this change, `SnapshotScanner.findBest` chose the largest version
candidate and `PlaintextNspPacker.pack` wrote **only that snapshot's**
`romfs/`. An extracted update can omit files that are unchanged from the
base. If the update's RomFS was packed alone, `main` could boot while the
game lacked base resources.

The packer now:

1. Scans the selected source tree for snapshots with ExeFS, NPDM and RomFS.
2. When the selected candidate lives in `updates/<version>/` or
   `update/<version>/`, finds a matching `base/` candidate in that tree
   with the **same title ID**.
3. Builds the output RomFS by enumerating the base first, then applying the
   selected update directory recursively. Updated files replace base
   files; nested directories retain base-only files. Name collisions between
   a file and directory respect the later layer. Large directories use
   per-directory name lookup instead of quadratic scans.
4. Uses the selected update ExeFS modules and version metadata, while
   embedding the resulting single merged RomFS in the existing plaintext NSP.
5. Validates that the effective DKB tree has `System/HeapSize` and `RSDB`,
   instead of testing the update tree in isolation. The source screen
   displays **base + update RomFS** when a pair was found.

No update data is fetched, decrypted or reconstructed. Both source trees
must already exist in the selected accessible directory. The merger does
not process deletion directives or specialized patch semantics; it is a
recursive file-overwrite merge, not bit-exact firmware patch installation.
A standalone update that does not expose a valid base in the chosen tree
is not silently assigned data from a different title.

## Expected directory layout

```text
MyExtractedGame/
  base/
    exefs/main
    exefs/main.npdm
    exefs/rtld
    exefs/sdk
    romfs/...
  updates/
    458752/
      exefs/main
      exefs/main.npdm
      exefs/rtld
      exefs/sdk
      romfs/...
```

A single already-merged `exefs/` + `romfs/` snapshot also works.

## Remaining verification

- Build 60 `NX2_ROMFS_PROBE` checks what the runtime **actually exposes**
  for DKB, rather than just whether the NCA was loadable.
- A later APK built from this change must be tested with a complete,
  user-owned extracted source tree and checked for the log's
  `NX2_ROMFS_PROBE ... System/HeapSize=true RSDB=true` markers.
- Successful checks would establish the expected files are mounted, **not**
  that game initialization, GPU emulation or gameplay is fully correct.
- Test a normal Switch 1 NSP to ensure adding overlay logic to the optional
  snapshot packer has not regressed existing game launches.
