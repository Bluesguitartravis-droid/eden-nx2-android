#!/usr/bin/env python3
"""Ocarina of Time foliage options in the game's Add-Ons list (build 284).

Three built-ins with no code patch, off until checked (like the Text Speed ones), applied when the
game starts:
- "Lighter Foliage (built-in)": the foliage layers' RomFS switches at lowest (EDEN_NX2_OOT_DETAIL=lowest;
  Hyrule Field on the Odin 3: about +8 %).
- "Performance Grass (built-in)": skips the tall grass's mesh draws (three mesh programs; Hyrule
  Field: 22.0 -> 25.1 fps). Ground cover, flowers and tree leaves stay.
- "No Grass or Foliage (built-in)": skips every mesh draw (EDEN_NX2_SKIP_MESH=all; 22.0 -> 27.6 fps).
EDEN_NX2_OOT_DETAIL and EDEN_NX2_SKIP_MESH, when set, take precedence. Needs apply-nx2-oot-detail.py
and apply-nx2-mesh-layout.py (their headers read nx2_foliage_addons.h).
"""
from pathlib import Path
import shutil
import sys

here = Path(__file__).resolve().parent
root = Path(sys.argv[1]).resolve() / "src"
shutil.copyfile(here / "nx2_foliage_addons.h", root / "video_core/nx2_foliage_addons.h")

path = root / "core/file_sys/patch_manager.cpp"
text = path.read_text()
pairs = [
    ('''#include "core/file_sys/patch_manager.h"
''', '''#include "core/file_sys/patch_manager.h"
#include "video_core/nx2_foliage_addons.h"
'''),
    ('''        .description = "Runs the game at 60 FPS, as on the console. A PC that can't hold 60 FPS "
                       "plays slow frames in slow motion; turn this off for the game's own 30 FPS "
                       "mode. Restart the game to apply.",
    },
};''', '''        .description = "Runs the game at 60 FPS, as on the console. A PC that can't hold 60 FPS "
                       "plays slow frames in slow motion; turn this off for the game's own 30 FPS "
                       "mode. Restart the game to apply.",
    },
    // Ocarina foliage options (nx2_foliage_addons.h): no code patch (no module), so PatchNSO and
    // HasNSOPatch never match them; RomFS (Nx2OotDetail) and the renderer (Nx2MeshStats) read them.
    BuiltinPatch{
        .name = Nx2FoliageAddons::kLighter,
        .title_id = Nx2FoliageAddons::kOcarina,
        .off_by_default = true,
        .description = "Turns off the moss, stone and undergrowth foliage layers. Grass, flowers "
                       "and trees stay. A few percent faster. Restart the game to apply.",
    },
    BuiltinPatch{
        .name = Nx2FoliageAddons::kPerformance,
        .title_id = Nx2FoliageAddons::kOcarina,
        .off_by_default = true,
        .description = "Hides the tall grass. Ground cover, flowers and tree leaves stay. About "
                       "14% faster in Hyrule Field on the AYN Odin 3. Restart the game to apply.",
    },
    BuiltinPatch{
        .name = Nx2FoliageAddons::kNone,
        .title_id = Nx2FoliageAddons::kOcarina,
        .off_by_default = true,
        .description = "Hides all grass, flowers and tree leaves drawn with mesh shaders: bare "
                       "ground and leafless trees. About 25% faster in Hyrule Field on the AYN "
                       "Odin 3. Restart the game to apply.",
    },
};'''),
]
for old, new in pairs:
    if text.count(old) != 1:
        sys.exit(f"apply-nx2-foliage-addons: expected one {old[:60]!r} in patch_manager.cpp, found {text.count(old)}")
    text = text.replace(old, new)
path.write_text(text)
print("apply-nx2-foliage-addons: applied")
