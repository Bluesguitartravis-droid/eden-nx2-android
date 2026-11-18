# Stage 1B.2 full ExeFS census — Odin 3 Ultra — 2026-11-02

Target title ID: \`040074a01bf12000\`

## Module set

- \`rtld\` — build \`9bd0b887b64847a610f78afe1699df3d\`
- \`main\` — build \`a1dd7480bca7b6e8fe4f71d25d3bab09\`
- \`sdk\` — build \`b7fff614f2bcddd38e80d571619c73f3\`

All 3 NSOs decompress exactly and their MOD0 headers validate.

## Rewrite census

| Site | rtld | main | sdk | total |
| --- | ---: | ---: | ---: | ---: |
| SVC | 7 | 0 | 72 | 79 |
| MRS TPIDR_EL0 | 0 | 499 | 2 | 501 |
| MSR TPIDR_EL0 | 0 | 0 | 3 | 3 |
| MRS TPIDRRO_EL0 | 0 | 0 | 428 | 428 |
| MRS CNTPCT_EL0 | 0 | 0 | 40 | 40 |
| MRS CTR_EL0 | 0 | 0 | 5 | 5 |

No CNTFRQ, CNTVCT, or dynamic stack-alignment sites were found.

The guest expects a 31.25 MHz counter while Android exposes 19.2 MHz, so trapped
CNTPCT reads must be scaled by 625/384.

## Stage 1C policy

The native host replaces Horizon's rtld role, matching nx2android's architecture.
Stage 1C therefore maps and relocates \`main\` + \`sdk\` directly and does not execute
the \`rtld\` module.

For the first execution proof:
- exact title/build IDs are mandatory;
- x18 is left native because the Odin probe showed it survives context switches and
  the host is compiled with \`-ffixed-x18\`;
- TPIDR/TPIDRRO/counter/cache-register instructions are replaced by controlled BRK
  traps and emulated by the host signal handler;
- SVCs and unresolved imports deliberately stop execution and are reported as the next
  HLE dependency instead of being allowed to become Android syscalls;
- synchronous faults are caught and reported with module + offset.

This is a bring-up harness, not a gameplay runtime.
