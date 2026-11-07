#!/usr/bin/env python3
"""Game list: on startup the cached list drops every library ExeFS game, because their paths
are plain file paths ("/storage/...main") and DocumentFile.fromSingleUri only checks content
URIs. Until the full rescan finished only container games (an .nup) showed. Check file paths
with File.exists()."""
from pathlib import Path
import sys

f = (Path(sys.argv[1]).resolve() /
     "src/android/app/src/main/java/org/yuzu/yuzu_emu/model/GamesViewModel.kt")
t = f.read_text()
old = """                                val gameExists =
                                    DocumentFile.fromSingleUri(
                                        YuzuApplication.appContext,
                                        Uri.parse(game.path)
                                    )?.exists()"""
new = """                                val gameExists = if (game.path.startsWith("/")) {
                                    java.io.File(game.path).exists()
                                } else {
                                    DocumentFile.fromSingleUri(
                                        YuzuApplication.appContext,
                                        Uri.parse(game.path)
                                    )?.exists()
                                }"""
if 'game.path.startsWith("/")' not in t:
    if old not in t:
        raise SystemExit("cached game existence check not found")
    f.write_text(t.replace(old, new, 1))
print("cached ExeFS games kept at startup")
