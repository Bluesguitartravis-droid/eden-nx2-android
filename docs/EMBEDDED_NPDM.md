# Embedded Switch 2 NPDM detection for generated NSPs

The SX2A/PokeDinner startup path decides whether a title is Switch 2 before it
creates the emulated system. Its upstream detector historically checks only for
`main.npdm` beside the opened file, which is naturally true for loose ExeFS
dumps but not for an NSP opened through Android's normal container/VFS path.

That can allow an otherwise parseable plaintext NSP to start with Switch 1
startup parameters, including the 19.2 MHz guest counter instead of Switch 2's
31.25 MHz counter and without the Switch 2 memory/settings path.

This fork adds a source-level fallback:

1. Keep the existing sibling `main.npdm` check for loose dumps.
2. For NSP files, parse the NSP with Eden's existing `FileSys::NSP`.
3. Find the Application/Program NCA.
4. Open its ExeFS.
5. Load its embedded `main.npdm` with the existing `ProgramMetadata` parser.
6. If `metadata.IsSwitch2()` is true, select the normal Switch 2 startup path.

This is deliberately scoped to plaintext/otherwise-readable NSPs. It does not
decrypt retail containers, generate keys, bypass encryption or change normal
Switch 1 NSP handling.

The behavior mirrors the purpose of NSP Box's experimental Windows probe, but is
implemented cleanly in source instead of patching executable machine code.
