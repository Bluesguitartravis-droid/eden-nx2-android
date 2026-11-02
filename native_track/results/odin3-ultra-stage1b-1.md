# Stage 1B census results — Odin 3 Ultra, Stage 1B build 1 (a7c59a8), 2026-11-02

## Donkey Kong Bananza (040074a01bf12000), main build a1dd7480bca7b6e8… — PASS

- LZ4: text 16405559 → 26771872, ro 3587549 → 6209536, data 872258 → 2970216; MOD0 at 0x1989060.
- `main` text: svc 0, mrs tpidr_el0 499, msr tpidr_el0 0, mrs tpidrro_el0 0, cntfrq/cntpct/cntvct 0.
- Expected: the OS layer (svc, counters, TLS setup) lives in the `sdk` module, which this
  build does not scan yet. `rtld` and `sdk` need to be added before the census is complete.
- `main`'s 499 tpidr_el0 reads read the guest thread pointer; tpidr_el0 is also Bionic's TLS
  register, so the host must swap it on every guest/host transition (or rewrite the reads).
- This build ID differs from nxnative's v458752 profile (main e25c90c1c136638885659fd55bf8a03b).

## Title 0400a3a01ee4c000 (folder picked by mistake; not the intended test), main 28684035 bytes — BLOCKED

    FAIL decompress_text: LZ4 output=0 expected=40110032
    FAIL decompress_ro: LZ4 output=0 expected=11274060
    FAIL decompress_data: stored bytes exceed NSO file

Likely cause: NSO flag bit 7 (0x80) = ZBIC, Nintendo's zstd variant (system 22.0.0+),
not LZ4 (see nxnative dm/tools/nso.py). The analyzer should print `flags` and branch on bit 7.
Decoder: Atmosphère's zstd.c as packaged by github.com/kinnay/zbic (single C file).
Licence caution: zbic and Atmosphère are GPLv2; check compatibility before linking into a
GPL-3.0-or-later binary.
