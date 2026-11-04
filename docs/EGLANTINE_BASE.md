# Eglantine NX2 Android base (builds 80+)

From build 84, `main` builds from **Eglantine** `nx2` at commit
`9a435b5` (github.com/zortos293/Eglantine, merged 2026-11-04), replacing the SX2A v0.3.2 base used
by build 69 (see `SX2A_BASE.md`). Eglantine carries the Switch 2 GPU, memory and service work that
continued after SX2A was archived; on the Odin 3 Ultra it gets Donkey Kong Bananza past the first
loading screen where SX2A v0.3.2 stalled.

## First confirmed result

Build 83 on an AYN Odin 3 Ultra (Adreno 830, bundled Turnip): **Mario Tennis Fever**
(`0400D4201C6EA000`, v1.1.1, loose folder) boots, plays its intro and reaches the title screen at
about 50 FPS, with no crash over the whole session.

## Patches applied in CI (in order)

| Step | Script | Why |
|---|---|---|
| Configure | `configure-eglantine.py` | App ID `dev.eden.eden_emulator`, version and update feed for this repo's releases |
| Loose ExeFS discovery | `apply-eglantine-switch2-regressions-tolerant.py` | Android game-folder scanning of loose ExeFS dumps. Sections whose upstream anchors moved (NSP Switch 2 detection among them) are skipped and reported as `patch-skip` warnings |
| Direct ExeFS launch | `apply-nx2-direct-exefs.py` | "Launch ExeFS game folder" picker, version chooser, base+update RomFS merge. Stops before upstream's `NewestProductTag` helper |
| Content guards | `apply-nx2-content-guards.py` | Decodes ZBIC frames behind a short prefix (no header flag), refuses undecodable code instead of running garbage, extra DKB diagnostics |
| Map budget | `apply-android-map-budget.py` | Android: GPU-only regions backed whole (upstream backs them per 4 MiB block); `NX2_MAPS` log of mappings, mapped bytes, largest free gap |
| Heap tracker budget | `apply-android-heap-tracker-budget.py` | Android: keep resident guest mappings to `max_map_count - 45000` (upstream `- 20000`), log evictions as `NX2_HEAP`. Targets the DKB loading crash |
| libc++ compatibility | `apply-android-atomic-ref-compat.py` | NDK libc++ lacks `std::atomic_ref` and `std::atomic<std::shared_ptr>`, used by newer dynarmic and WindowTrace |

## Known limits

- Launch Switch 2 games as **loose folders** (`exefs/` + `romfs/`, or `base/` + `updates/<version>/`).
  Switch 2 detection inside NSPs is not applied on this base yet.
- Use the **bundled Turnip** driver; the stock Qualcomm driver crashed in its shader compiler.
- Donkey Kong Bananza crashed 50-70 s into loading on builds 80-83, every time in a failed memory
  mapping (scudo `reportMapError`, `std::bad_alloc` in dynarmic) with address space and commit to
  spare. Build 84's heap-tracker budget is the fix under test.
- One compute shader in DKB does not translate (`SPIR-V Instruction is not implemented`); its dispatch
  is skipped.
