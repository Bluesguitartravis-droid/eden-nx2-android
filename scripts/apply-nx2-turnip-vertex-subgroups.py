#!/usr/bin/env python3
"""Turnip: no subgroup operations in the vertex and tessellation stages (Mario Kart World stripes).

Mario Kart World on Turnip (every build tried: Mesa 26.2.99, 26.99.99 and others) draws huge
flat triangles and long streaks across the screen; on Qualcomm's driver it does not, and none of
the emulator's own switches changed it. Turnip on the Adreno 8xx reports subgroup operations in
every stage with a subgroup size of 64-128 that a vertex stage cannot pin to the guest's 32-lane
warp, while Qualcomm's driver has subgroup operations only in fragment and compute shaders. A
vertex shader whose warp votes, ballots or shuffles span 64-128 vertices reads other vertices'
values: positions go wrong. Banana Native found the same on Turnip (geometry stage, Donkey Kong
Bananza) and Lichii already leaves the geometry stage out on Turnip.

On Turnip the vertex and tessellation stages are now left out as well, as on Qualcomm's driver.
EDEN_NX2_TURNIP_VERTEX_SUBGROUPS=1 keeps them (the old behaviour).
Usage: python3 scripts/apply-nx2-turnip-vertex-subgroups.py <upstream-root>
"""
from pathlib import Path
import sys

path = Path(sys.argv[1]) / "src/video_core/renderer_vulkan/vk_pipeline_cache.cpp"
text = path.read_text()
MARK = "NX2 Turnip vertex subgroups"
if MARK in text:
    print("already patched")
    raise SystemExit(0)
old = ("    if (driver_id == VK_DRIVER_ID_MESA_TURNIP) {\n"
       "        supported_subgroup_stages &= ~(1u << static_cast<u32>(Shader::Stage::Geometry));\n"
       "    }\n")
if text.count(old) != 1:
    raise SystemExit("vk_pipeline_cache.cpp: Turnip geometry subgroup rule not found")
new = old + r'''    if (driver_id == VK_DRIVER_ID_MESA_TURNIP) {
        // NX2 Turnip vertex subgroups: 64-128 lanes that a vertex stage cannot pin to the guest's
        // 32 put other vertices' values into warp votes and shuffles (Mario Kart World's stripes).
        const char* const keep{std::getenv("EDEN_NX2_TURNIP_VERTEX_SUBGROUPS")};
        if (keep == nullptr || keep[0] != '1') {
            for (const Shader::Stage stage : {Shader::Stage::VertexA, Shader::Stage::VertexB,
                                              Shader::Stage::TessellationControl,
                                              Shader::Stage::TessellationEval}) {
                supported_subgroup_stages &= ~(1u << static_cast<u32>(stage));
            }
            LOG_INFO(Render_Vulkan, "NX2: Turnip: no subgroup operations in vertex and "
                                    "tessellation shaders (EDEN_NX2_TURNIP_VERTEX_SUBGROUPS=1 keeps them)");
        }
    }
'''
path.write_text(text.replace(old, new, 1))
print("Turnip vertex subgroups left out")
