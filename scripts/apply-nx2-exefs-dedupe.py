#!/usr/bin/env python3
"""Library: a loose ExeFS dump and a .nup/.nsp of the same game showed twice. The container's
program ID can be the update's (base | 0x800) while the dump's NPDM holds the base ID; compare
with the update bit cleared, as the container scan already does."""
from pathlib import Path
import sys

f = Path(sys.argv[1]).resolve() / "src/android/app/src/main/java/io/github/zortos293/lichii/utils/GameHelper.kt"
t = f.read_text()
old = """            val duplicate = games.any {
                it.path == game.path ||
                    (game.programId.isNotEmpty() && it.programId == game.programId)
            }"""
new = """            val baseId = { id: String -> id.toLongOrNull()?.let { it and 0x800L.inv() } }
            val duplicate = games.any {
                it.path == game.path ||
                    (game.programId.isNotEmpty() &&
                        (it.programId == game.programId ||
                            (baseId(game.programId) != null &&
                                baseId(it.programId) == baseId(game.programId))))
            }"""
if "baseId(game.programId)" not in t:
    if old not in t:
        raise SystemExit("addLibraryExeFSGames duplicate check not found")
    f.write_text(t.replace(old, new, 1))
print("library ExeFS dedupe by base program ID")
