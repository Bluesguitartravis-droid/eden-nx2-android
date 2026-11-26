#!/usr/bin/env python3
"""Mesh emulation: contiguous output layout, per-pipeline stats and a live skip (build 283).

Ocarina's grass and foliage are task/mesh programs; Turnip has no VK_EXT_mesh_shader on the Adreno,
so Eden runs each mesh workgroup (32 invocations) as a compute shader that writes a block of
vertex outputs, then draws them with a vertex shader. Switching all of them off
(EDEN_NX2_NO_MESH=1) took Hyrule Field from 19.8 to 27.7 fps on the Odin 3.

1. Line-major blocks (EDEN_NX2_MESH_LAYOUT, read once per app start; default on, =0 is the old
   layout). Line l of vertex v went to word v * vertex_lines + l: the 32 invocations of one store
   wrote 32 words vertex_lines apart (a cache line each), and the vertex shader read them the same
   way. Now it is l * max_vertices + v (and l * max_primitives + p for per-primitive lines): one
   store writes 32 consecutive words. Block size and the index region are unchanged. The compute
   epilogue, the emulation vertex shader and the readback probe use the same layout; translated
   SPIR-V is never stored on disk (the driver's cache is keyed by the SPIR-V), so builds and the
   switch can change it freely.
2. nx2_mesh_stats.h: EDEN_NX2_MESH_STATS=1 (live) logs every 10 s which mesh pipelines are drawn,
   how many workgroups each launches and their output layout; EDEN_NX2_SKIP_MESH=<hash,...>|all
   (live) skips the draws of the listed pipelines, to measure each one's cost.
"""
from pathlib import Path
import shutil
import sys

here = Path(__file__).resolve().parent
root = Path(sys.argv[1]).resolve() / "src"


def patch(name, pairs):
    path = root / name
    text = path.read_text()
    for old, new in pairs:
        if text.count(old) != 1:
            sys.exit(f"apply-nx2-mesh-layout: expected one {old[:70]!r} in {name}, found {text.count(old)}")
        text = text.replace(old, new)
    path.write_text(text)


shutil.copyfile(here / "nx2_mesh_stats.h", root / "video_core/nx2_mesh_stats.h")

patch("shader_recompiler/backend/spirv/emit_spirv.h", [
    ('''#pragma once

#include <vector>
''', '''#pragma once

#include <cstdlib>
#include <vector>
'''),
    ('''/// Mesh emulation without VK_EXT_mesh_shader. The mesh program runs as a compute shader
''', '''/// Mesh emulation output blocks line-major (EDEN_NX2_MESH_LAYOUT, read once; =0 restores the old
/// vertex-major layout): line l of vertex v at l * max_vertices + v, line l of primitive p at
/// l * max_primitives + p, so that the 32 invocations of a store write consecutive words. The
/// block's regions and size are the same in both layouts.
[[nodiscard]] inline bool MeshEmulationLineMajor() {
    static const bool line_major{[] {
        const char* const value{std::getenv("EDEN_NX2_MESH_LAYOUT")};
        return value == nullptr || value[0] != '0';
    }()};
    return line_major;
}

/// Mesh emulation without VK_EXT_mesh_shader. The mesh program runs as a compute shader
'''),
])

patch("shader_recompiler/backend/spirv/emit_spirv_mesh.cpp", [
    # Compute epilogue: vertex lines.
    ('''            const Id destination{ctx.OpIAdd(
                ctx.U32[1], block, ctx.OpIMul(ctx.U32[1], vertex, ctx.Const(vertex_lines)))};
            for (u32 line = 0; line < vertex_lines; ++line) {
                const Id word{ctx.OpLoad(
                    ctx.U32[1],
                    SharedWord(ctx, ctx.mesh_attributes,
                               ctx.OpIAdd(ctx.U32[1], source, ctx.Const(line * 32))))};
                ctx.OpStore(OutputWord(ctx, ctx.OpIAdd(ctx.U32[1], destination, ctx.Const(line))),
                            word);
            }''', '''            // Line-major (MeshEmulationLineMajor): line l at l * max_vertices + v.
            const bool line_major{MeshEmulationLineMajor()};
            const Id destination{
                line_major ? ctx.OpIAdd(ctx.U32[1], block, vertex)
                           : ctx.OpIAdd(ctx.U32[1], block,
                                        ctx.OpIMul(ctx.U32[1], vertex, ctx.Const(vertex_lines)))};
            const u32 line_stride{line_major ? mesh.max_vertices : 1U};
            for (u32 line = 0; line < vertex_lines; ++line) {
                const Id word{ctx.OpLoad(
                    ctx.U32[1],
                    SharedWord(ctx, ctx.mesh_attributes,
                               ctx.OpIAdd(ctx.U32[1], source, ctx.Const(line * 32))))};
                ctx.OpStore(OutputWord(ctx, ctx.OpIAdd(ctx.U32[1], destination,
                                                       ctx.Const(line * line_stride))),
                            word);
            }'''),
    # Compute epilogue: primitive lines.
    ('''            const Id line_destination{
                ctx.OpIAdd(ctx.U32[1], primitive_line_base,
                           ctx.OpIMul(ctx.U32[1], primitive, ctx.Const(primitive_lines)))};
            for (u32 line = 0; line < primitive_lines; ++line) {
                const Id word{ctx.OpLoad(
                    ctx.U32[1], SharedWord(ctx, ctx.mesh_attributes,
                                           ctx.OpIAdd(ctx.U32[1], source, ctx.Const(line * 32))))};
                ctx.OpStore(
                    OutputWord(ctx, ctx.OpIAdd(ctx.U32[1], line_destination, ctx.Const(line))),
                    word);
            }''', '''            // Line-major (MeshEmulationLineMajor): line l at l * max_primitives + p.
            const bool line_major{MeshEmulationLineMajor()};
            const Id line_destination{
                line_major
                    ? ctx.OpIAdd(ctx.U32[1], primitive_line_base, primitive)
                    : ctx.OpIAdd(ctx.U32[1], primitive_line_base,
                                 ctx.OpIMul(ctx.U32[1], primitive, ctx.Const(primitive_lines)))};
            const u32 line_stride{line_major ? mesh.max_primitives : 1U};
            for (u32 line = 0; line < primitive_lines; ++line) {
                const Id word{ctx.OpLoad(
                    ctx.U32[1], SharedWord(ctx, ctx.mesh_attributes,
                                           ctx.OpIAdd(ctx.U32[1], source, ctx.Const(line * 32))))};
                ctx.OpStore(OutputWord(ctx, ctx.OpIAdd(ctx.U32[1], line_destination,
                                                       ctx.Const(line * line_stride))),
                            word);
            }'''),
    # Emulation vertex shader: vertex lines.
    ('''    const Id vertex_base{
        module.OpIAdd(u32_type, block, module.OpIMul(u32_type, vertex, constant(vertex_lines)))};
''', '''    // The compute epilogue's layout (MeshEmulationLineMajor).
    const bool line_major{MeshEmulationLineMajor()};
    const Id vertex_base{
        line_major
            ? module.OpIAdd(u32_type, block, vertex)
            : module.OpIAdd(u32_type, block, module.OpIMul(u32_type, vertex, constant(vertex_lines)))};
    const u32 vertex_line_stride{line_major ? mesh.max_vertices : 1U};
'''),
    ('''        const Id value{module.OpBitcast(
            f32_type, load(module.OpIAdd(u32_type, vertex_base, constant(line))))};''',
     '''        const Id value{module.OpBitcast(
            f32_type,
            load(module.OpIAdd(u32_type, vertex_base, constant(line * vertex_line_stride))))};'''),
    # Emulation vertex shader: primitive lines.
    ('''                      constant(mesh.max_vertices * vertex_lines + mesh.max_primitives * corners)),
        module.OpIMul(u32_type, primitive, constant(primitive_lines)))};''',
     '''                      constant(mesh.max_vertices * vertex_lines + mesh.max_primitives * corners)),
        line_major ? primitive : module.OpIMul(u32_type, primitive, constant(primitive_lines)))};
    const u32 primitive_line_stride{line_major ? mesh.max_primitives : 1U};'''),
    ('''        const Id word{load(module.OpIAdd(u32_type, primitive_line_base, constant(line)))};''',
     '''        const Id word{load(
            module.OpIAdd(u32_type, primitive_line_base, constant(line * primitive_line_stride)))};'''),
])

patch("video_core/renderer_vulkan/vk_rasterizer.cpp", [
    ('''#include "shader_recompiler/backend/spirv/emit_spirv.h"
''', '''#include "shader_recompiler/backend/spirv/emit_spirv.h"
#include "video_core/nx2_mesh_stats.h"
'''),
    # The readback probe reads the same layout.
    ('''                result[element] = std::bit_cast<f32>(word(base + u64{vertex} * probe.vertex_lines +
                                                          line));''',
     '''                result[element] = std::bit_cast<f32>(word(
                    Shader::Backend::SPIRV::MeshEmulationLineMajor()
                        ? base + u64{line} * probe.max_vertices + vertex
                        : base + u64{vertex} * probe.vertex_lines + line));'''),
    # Per-pipeline stats and the live skip.
    ('''    const bool emulated_mesh{mesh_draw && pipeline->IsEmulatedMesh()};
    if (mesh_draw &&
        (no_mesh || (!device.IsExtMeshShaderSupported() && !emulated_mesh) ||
         (emulated_mesh && is_indirect))) {
        report_failure(no_mesh         ? "EDEN_NX2_NO_MESH"
                       : emulated_mesh ? "indirect emulated mesh draw"
                                       : "no VK_EXT_mesh_shader");''',
     '''    const bool emulated_mesh{mesh_draw && pipeline->IsEmulatedMesh()};
    // EDEN_NX2_MESH_STATS / EDEN_NX2_SKIP_MESH (live, nx2_mesh_stats.h).
    bool skip_listed{};
    if (emulated_mesh) {
        const MeshEmulationSetup* const setup{pipeline->MeshEmulation()};
        VideoCommon::Nx2MeshStats::Shape shape{};
        if (setup != nullptr) {
            shape = {setup->max_vertices, setup->vertex_lines, setup->max_primitives,
                     setup->block_words, setup->has_task};
        }
        const u64 stat_groups{
            is_indirect ? 0
                        : u64{MakeDrawParams(maxwell3d->draw_manager.draw_state, 1, is_indexed)
                                  .num_vertices}};
        skip_listed = VideoCommon::Nx2MeshStats::Note(pipeline, pipeline->ShaderHashes(), shape,
                                                      stat_groups, is_indirect);
    }
    if (mesh_draw &&
        (no_mesh || skip_listed || (!device.IsExtMeshShaderSupported() && !emulated_mesh) ||
         (emulated_mesh && is_indirect))) {
        report_failure(no_mesh         ? "EDEN_NX2_NO_MESH"
                       : skip_listed   ? "EDEN_NX2_SKIP_MESH"
                       : emulated_mesh ? "indirect emulated mesh draw"
                                       : "no VK_EXT_mesh_shader");'''),
])
print("apply-nx2-mesh-layout: applied")
