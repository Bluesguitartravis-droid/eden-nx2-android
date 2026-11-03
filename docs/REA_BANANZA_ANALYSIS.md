# REA plan for NX2 / Donkey Kong Bananza

This repo uses REA only as a reverse-engineering aid. It does not replace Eden/Eglantine emulation.

## Target

The current high-value static target is the guest call chain that precedes the repeatable Android failure:

- `SilverStar.nss + 0x5AF1C8`
- caller chain: `+0x5AE400 -> +0x5AD7EC -> nnMain+0x5AC454`
- runtime failure: near-null execution target `0x8` / PC `0xC`

The exact guest revision matters. The observed build IDs were:

- main: `A1DD7480BCA7B6E8FE4F71D25D3BAB09...`
- sdk: `B7FFF614F2BCDDD38E80D571619C73F3...`

Do not substitute a different title/update binary and assume offsets are equivalent.

## Why REA helps

REA can orchestrate Ghidra/Hopper/IDA/Kuna and collect evidence for:

- the exact instruction at/around the crash-chain offset;
- containing function and pseudocode;
- xrefs and callers;
- indirect-call targets and unresolved dispatch;
- comparison against another executable revision.

This is especially relevant because newer Eglantine fixes multiple NX2 services where a generic stub returned success but no IPC object. The guest then called through a null object/vtable.

## Input

REA does not advertise Nintendo NSO as a first-class format. Prefer a provider-readable AArch64 ELF/load image reconstructed from the exact `main` being tested.

Keep retail game binaries private. Do not commit them to this repository.

## Run

Install and configure REA/Ghidra, then:

```bash
scripts/rea-bananza-analysis.sh /private/path/to/main.elf 0x5af1c8
```

Set `REA_PROVIDER` to choose another configured native provider.

The output directory is intended to be shareable evidence without redistributing the guest binary.
