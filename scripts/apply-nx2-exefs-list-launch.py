#!/usr/bin/env python3
"""Game list: tapping a loose ExeFS game did nothing but show "file not found" and start a
rescan (the loading bubble), so each launch needed "Launch ExeFS game folder" and a folder pick.

GameAdapter.onClick checks that the game still exists with DocumentFile.fromSingleUri, which only
understands content:// URIs. Library ExeFS games are plain file paths
("/storage/emulated/0/.../exefs/main"), for which it always answers false. Check those with
File.exists(), as the cached list already does (apply-nx2-cached-exefs-games.py)."""
from pathlib import Path
import sys

f = (Path(sys.argv[1]).resolve() /
     "src/android/app/src/main/java/org/yuzu/yuzu_emu/adapters/GameAdapter.kt")
t = f.read_text()
old = """            val gameExists = DocumentFile.fromSingleUri(
                YuzuApplication.appContext,
                game.path.toUri()
            )?.exists() == true
"""
new = """            // NX2: loose ExeFS games are plain file paths, which DocumentFile cannot check.
            val gameExists = if (game.path.startsWith("/")) {
                java.io.File(game.path).exists()
            } else {
                DocumentFile.fromSingleUri(
                    YuzuApplication.appContext,
                    game.path.toUri()
                )?.exists() == true
            }
"""
if 'game.path.startsWith("/")' not in t:
    if old not in t:
        raise SystemExit("GameAdapter.onClick existence check not found")
    f.write_text(t.replace(old, new, 1))

# .nx archives (upstream's single-file game format, made with Lichii on a PC) show in the list.
g = Path(sys.argv[1]).resolve() / "src/android/app/src/main/java/org/yuzu/yuzu_emu/model/Game.kt"
gt = g.read_text()
if '"nup", "nx")' not in gt and '"xcz", "nup")' in gt:
    g.write_text(gt.replace('"xcz", "nup")', '"xcz", "nup", "nx")', 1))
print("library ExeFS games launch from the list")
