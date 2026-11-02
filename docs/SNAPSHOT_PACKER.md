# Extracted snapshot -> plaintext NSP packer

This fork includes an experimental Android tool inspired by the MIT-licensed
NSP Box v3.0.0 container writer.

## Scope

The tool accepts a complete, already-extracted snapshot containing:

```text
Game/
  exefs/
    main
    main.npdm
    ...
  romfs/
    ...
```

It scans up to four directory levels so `base/` and numeric update folders can
be selected from a parent game folder. The newest numeric snapshot is preferred.

The output is an **unsigned plaintext emulator NSP**, plus the original
`main.npdm` beside it. The companion file preserves the PokeDinner/SX2A
Switch 2 detection path without using NSP Box's Windows executable patcher.

## Explicit non-goals

The Android feature does not:

- decrypt encrypted NSP/XCI or other retail containers;
- use or generate Nintendo retail keys, tickets, certificates or signatures;
- download games or artwork;
- reconstruct authenticated cartridge images;
- merge patch-only/BKTR updates.

It is intended for users packaging complete extracted data they are authorized
to use.

## Implementation

The format writer is a Kotlin streaming port of the relevant NSP Box concepts:

- PFS0 package creation;
- plaintext NCA Program/Control/Meta containers;
- ExeFS PFS0 and RomFS/IVFC integrity trees;
- Application CNMT records;
- SHA-256 content IDs.

Large game data is streamed from Android's Storage Access Framework. No
full-game temporary copy is created. Building still reads large source files
more than once because the final PFS0 filenames/CNMT require content hashes
before the package header can be emitted.

NSP Box's original application code is MIT licensed. Its license notice is
copied into the APK at `assets/licenses/NSP-Box-LICENSE.txt`.
