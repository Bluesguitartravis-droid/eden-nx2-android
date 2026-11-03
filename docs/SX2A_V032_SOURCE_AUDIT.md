# NX2 Android SX2A v0.3.2 source audit

Official reference:
- https://git.eden-emu.dev/moceure/eden-nx2-android/releases/tag/nx2-android-v0.3.2
- Official release source commit `dd3cb327c79f511830e460b916a45a33b5738759`.
- Project archived on 2026-11-02. GPLv3 or later; preserve credits and provenance.

## What the release proves (and does not)

The release is built on PokeDinner 2.0.0 plus Android modifications. Its author
reports progress on a Snapdragon SM8850 / Adreno 840 phone with 16 GiB RAM;
that **does not establish gameplay performance on the AYN Odin 3 Ultra**.
A build reaching gameplay does not imply every game revision, update package,
or user-supplied extracted tree is compatible.

The published release notes focus on frame pacing, shaders, sparse textures,
a phone-GPU memory cap, NUP files and library scanning. Earlier releases
also introduced Android-first Switch 2 settings and GPU support.

## Confirmed differences from our pinned Eglantine 3bdd53d

**High priority: vi2 client lifecycle**

The v0.3.2 `INx2Vi2Session` registers:

- TIPC 11: persistent notification event (instead of an event never signalled).
- TIPC 21: drain the notification bitmap.
- TIPC 101/102: display open/close, retirement notification.
- TIPC 201/202: register/unregister client tokens, notify the render thread.
- TIPC 212: available buffer mask `0x7fff`.

Our pinned revision **did not implement TIPC 201 or 21** and returned
generic zeroed/stub responses for 101/102/202. Our implementation of 212
already returns `0x7fff` and 31/32 use a newer native-event model. Do not
replace the latter with v0.3.2's older handlers.

Build 65 ports only the coherent notification lifecycle, with
`NX2_VI2_CLIENT_REGISTER` and `NX2_VI2_CLIENT_RETIRE` markers.
The original Android logs reported vi2 201 auto-stubs, making this a
specific diagnostic A/B rather than a speculative wholesale rebase.

**Performance: important but later**

- The exact release's `src/video_core/macro_turing_jit.h` has an
  `ARCHITECTURE_arm64` Oaknut backend. Our pinned file lacks it. The
  source author reports roughly 5x speed over the interpreter for macro
  execution, not a guarantee of 5x game FPS.
- The release describes sizable ARM64 Dynarmic metadata and thread/fiber
  stack memory savings. Validate these with memory traces on the Odin
  before porting; allocator changes are too invasive for the boot-debug
  build.
- The release carries Adreno limits, occlusion-query workarounds,
  additional QMD layouts, HSETP2 shader lowering, sparse-texture and
  frame-pacing fixes. Compare individual paths before transplanting,
  since our pinned Eglantine contains newer, different implementations.

**Content and format**

- The release explicitly supports loose `exefs/main` with sibling `romfs/`
  and NUP files; it does not claim ordinary retail NSP/XCI launch for
  Switch 2 games.
- Our Build 64 now provides explicit direct loose-ExeFS launching and can
  merge extracted `base/romfs` and `updates/<version>/romfs` in memory.
- Our original `base.nsp` passed NPDM/NSO startup but mounted
  `System/HeapSize=false` and `RSDB=false`. No service change can
  manufacture missing game resources. Test the **same** already-extracted
  base/update contents under both launchers before attributing a crash to
  emulation differences.

## Test order

1. Validate that Build 65 compiles and that both vi2 markers exist.
2. Launch a complete extracted DKB tree using **Launch ExeFS game folder**,
   *not* the old `base.nsp`; verify `NX2_LOOSE_ROMFS mode=base+update`.
3. Compare `NX2_ROMFS_PROBE` path presence and `NX2_VI2_CLIENT_REGISTER`
   and `NX2_DKB_FIRST_FAULT` against Build 64.
4. Only if title resources mount and the first guest fault persists,
   investigate service/CPU differences from PokeDinner and the release.
5. Once a title presents frames, separately test the ARM64 macro JIT,
   memory optimizations and Adreno changes for FPS/VRAM stability.

Build 65 is an evidence-backed service compatibility test, **not** a
promise that any particular game will reach a playable frame.
