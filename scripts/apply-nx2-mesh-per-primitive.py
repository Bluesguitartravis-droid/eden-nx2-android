#!/usr/bin/env python3
"""Mesh emulation with per-primitive outputs (Mario Kart World's dropped mesh draws).

Upstream's mesh emulation (no VK_EXT_mesh_shader: the mesh program runs as a compute shader
that writes one block per workgroup, then a vertex shader pulls a triangle list from it)
refuses mesh programs with per-primitive outputs, so their draws are dropped. In Mario Kart
World 8 of 12 mesh pipeline types have them (about 55,000 dropped draws a minute: striped
and missing scenery).

This keeps the same design and adds the per-primitive lines:
- block layout: vertices, then max_primitives * 3 indices, then max_primitives *
  primitive_lines words (primitive p, line l at p * primitive_lines + l);
- the compute epilogue copies them from workgroup memory;
- the emulation's vertex shader knows its primitive (it draws one triangle per primitive slot),
  so it writes each per-primitive generic to all three corners as a flat output, and Layer /
  ViewportIndex as vertex outputs (VK_EXT_shader_viewport_index_layer);
- the fragment program reads those generics Flat instead of PerPrimitiveEXT.
PrimitiveId written per primitive is not passed on (the fragment sees the draw's own id).
Other per-primitive attributes keep the pipeline unsupported and are logged.

EDEN_NX2_MESH_PER_PRIMITIVE=0 restores upstream behaviour (those draws dropped).
Usage: python3 scripts/apply-nx2-mesh-per-primitive.py <upstream-root>
"""
from pathlib import Path
import sys

root = Path(sys.argv[1])
MARK = "NX2 per-primitive mesh emulation"


def patch(rel, edits):
    path = root / rel
    text = path.read_text()
    if MARK in text:
        print(f"{rel}: already patched")
        return
    for old, new in edits:
        if text.count(old) != 1:
            raise SystemExit(f"{rel}: anchor found {text.count(old)} times:\n{old}")
        text = text.replace(old, new, 1)
    path.write_text(text)
    print(f"{rel}: patched")


# Block layout.
patch("src/shader_recompiler/backend/spirv/emit_spirv.h", [(
    "    return mesh.max_vertices * static_cast<u32>(mesh.vertex_lines.size()) +\n"
    "           mesh.max_primitives * 3;\n",
    "    // " + MARK + ": then max_primitives * primitive_lines words\n"
    "    // (primitive p, line l at p * primitive_lines + l).\n"
    "    return mesh.max_vertices * static_cast<u32>(mesh.vertex_lines.size()) +\n"
    "           mesh.max_primitives * (3 + static_cast<u32>(mesh.primitive_lines.size()));\n",
)])

# Runtime info: fragment generics that arrive flat from the emulation's vertex shader.
patch("src/shader_recompiler/runtime_info.h", [(
    "    std::bitset<32> per_primitive_generics{};\n",
    "    std::bitset<32> per_primitive_generics{};\n"
    "    /// " + MARK + ": fragment inputs written per primitive by an emulated\n"
    "    /// mesh program, which reach the fragment stage as flat vertex outputs.\n"
    "    std::bitset<32> flat_generics{};\n",
)])

patch("src/shader_recompiler/backend/spirv/spirv_emit_context.cpp", [(
    "        if (runtime_info.per_primitive_generics.test(index)) {\n",
    "        if (runtime_info.flat_generics.test(index)) {\n"
    "            // " + MARK + ": the same value on all three corners.\n"
    "            Decorate(id, spv::Decoration::Flat);\n"
    "            continue;\n"
    "        }\n"
    "        if (runtime_info.per_primitive_generics.test(index)) {\n",
)])

mesh_cpp = "src/shader_recompiler/backend/spirv/emit_spirv_mesh.cpp"
patch(mesh_cpp, [
    # Compute epilogue: copy the per-primitive lines after the index list.
    (
        "            If(ctx, ctx.OpULessThan(ctx.U1, primitive, ctx.Const(mesh.max_primitives)), write);\n"
        "        } else {\n"
        "            write();\n"
        "        }\n"
        "    }\n"
        "}\n",
        "            If(ctx, ctx.OpULessThan(ctx.U1, primitive, ctx.Const(mesh.max_primitives)), write);\n"
        "        } else {\n"
        "            write();\n"
        "        }\n"
        "    }\n"
        "    // " + MARK + ": primitive p, line l at\n"
        "    // index_base + max_primitives * 3 + p * primitive_lines + l.\n"
        "    const u32 primitive_lines{static_cast<u32>(mesh.primitive_lines.size())};\n"
        "    if (primitive_lines != 0) {\n"
        "        const Id primitive_base{\n"
        "            ctx.OpIAdd(ctx.U32[1], index_base, ctx.Const(mesh.max_primitives * 3))};\n"
        "        for (u32 first = 0; first < mesh.max_primitives; first += threads) {\n"
        "            const Id primitive{ctx.OpIAdd(ctx.U32[1], invocation, ctx.Const(first))};\n"
        "            const auto copy = [&] {\n"
        "                const Id group{ctx.OpShiftRightLogical(ctx.U32[1], primitive, ctx.Const(5U))};\n"
        "                const Id slot{ctx.OpBitwiseAnd(ctx.U32[1], primitive, ctx.Const(31U))};\n"
        "                const Id source{ctx.OpIAdd(\n"
        "                    ctx.U32[1], ctx.OpIMul(ctx.U32[1], group, ctx.Const(primitive_lines * 32)),\n"
        "                    ctx.OpIAdd(ctx.U32[1], slot, ctx.Const(ctx.mesh_primitive_base)))};\n"
        "                const Id destination{ctx.OpIAdd(\n"
        "                    ctx.U32[1], primitive_base,\n"
        "                    ctx.OpIMul(ctx.U32[1], primitive, ctx.Const(primitive_lines)))};\n"
        "                for (u32 line = 0; line < primitive_lines; ++line) {\n"
        "                    const Id word{ctx.OpLoad(\n"
        "                        ctx.U32[1],\n"
        "                        SharedWord(ctx, ctx.mesh_attributes,\n"
        "                                   ctx.OpIAdd(ctx.U32[1], source, ctx.Const(line * 32))))};\n"
        "                    ctx.OpStore(\n"
        "                        OutputWord(ctx, ctx.OpIAdd(ctx.U32[1], destination, ctx.Const(line))),\n"
        "                        word);\n"
        "                }\n"
        "            };\n"
        "            if (first + threads > mesh.max_primitives) {\n"
        "                If(ctx, ctx.OpULessThan(ctx.U1, primitive, ctx.Const(mesh.max_primitives)),\n"
        "                   copy);\n"
        "            } else {\n"
        "                copy();\n"
        "            }\n"
        "        }\n"
        "    }\n"
        "}\n",
    ),
    # Vertex shader: per-primitive outputs.
    (
        "    if (profile.supported_spirv >= 0x00010400) {\n"
        "        interfaces.push_back(buffer);\n",
        "    // " + MARK + ": generics written per primitive go to all three corners\n"
        "    // (flat), Layer and ViewportIndex become vertex outputs.\n"
        "    std::array<Id, IR::NUM_GENERICS> primitive_generics{};\n"
        "    Id primitive_layer{}, primitive_viewport{};\n"
        "    const Id output_s32{module.TypePointer(spv::StorageClass::Output, s32_type)};\n"
        "    for (const u32 line : mesh.primitive_lines) {\n"
        "        const IR::Attribute attribute{line};\n"
        "        if (IR::IsGeneric(attribute)) {\n"
        "            const u32 index{IR::GenericAttributeIndex(attribute)};\n"
        "            if (!Sirit::ValidId(generics[index]) && !Sirit::ValidId(primitive_generics[index])) {\n"
        "                primitive_generics[index] =\n"
        "                    module.AddGlobalVariable(output_vec4, spv::StorageClass::Output);\n"
        "                module.Decorate(primitive_generics[index], spv::Decoration::Location, index);\n"
        "                module.Decorate(primitive_generics[index], spv::Decoration::Flat);\n"
        "                interfaces.push_back(primitive_generics[index]);\n"
        "            }\n"
        "            continue;\n"
        "        }\n"
        "        const bool layer{attribute == IR::Attribute::Layer};\n"
        "        if (!layer && attribute != IR::Attribute::ViewportIndex) {\n"
        "            continue; // PrimitiveId: the fragment stage sees the draw's own\n"
        "        }\n"
        "        Id& variable{layer ? primitive_layer : primitive_viewport};\n"
        "        if (!Sirit::ValidId(variable)) {\n"
        "            module.AddExtension(\"SPV_EXT_shader_viewport_index_layer\");\n"
        "            module.AddCapability(spv::Capability::ShaderViewportIndexLayerEXT);\n"
        "            variable = module.AddGlobalVariable(output_s32, spv::StorageClass::Output);\n"
        "            module.Decorate(variable, spv::Decoration::BuiltIn,\n"
        "                            layer ? spv::BuiltIn::Layer : spv::BuiltIn::ViewportIndex);\n"
        "            interfaces.push_back(variable);\n"
        "        }\n"
        "    }\n"
        "    if (profile.supported_spirv >= 0x00010400) {\n"
        "        interfaces.push_back(buffer);\n",
    ),
    (
        "    module.OpStore(position, default_vec4);\n"
        "    for (const Id generic : generics) {\n"
        "        if (Sirit::ValidId(generic)) {\n"
        "            module.OpStore(generic, default_vec4);\n"
        "        }\n"
        "    }\n",
        "    module.OpStore(position, default_vec4);\n"
        "    for (const Id generic : generics) {\n"
        "        if (Sirit::ValidId(generic)) {\n"
        "            module.OpStore(generic, default_vec4);\n"
        "        }\n"
        "    }\n"
        "    for (const Id generic : primitive_generics) {\n"
        "        if (Sirit::ValidId(generic)) {\n"
        "            module.OpStore(generic, default_vec4);\n"
        "        }\n"
        "    }\n",
    ),
    (
        "    // OpenGL-style clip space, as the mesh epilogue's ConvertDepth: z = (z + w) / 2.\n",
        "    // " + MARK + ": this triangle's per-primitive words.\n"
        "    if (const u32 primitive_lines{static_cast<u32>(mesh.primitive_lines.size())};\n"
        "        primitive_lines != 0) {\n"
        "        const Id primitive_words{module.OpIAdd(\n"
        "            u32_type,\n"
        "            module.OpIAdd(u32_type, block,\n"
        "                          constant(mesh.max_vertices * vertex_lines + mesh.max_primitives * 3)),\n"
        "            module.OpIMul(u32_type, primitive, constant(primitive_lines)))};\n"
        "        for (u32 line = 0; line < primitive_lines; ++line) {\n"
        "            const IR::Attribute attribute{mesh.primitive_lines[line]};\n"
        "            const Id word{load(module.OpIAdd(u32_type, primitive_words, constant(line)))};\n"
        "            if (IR::IsGeneric(attribute)) {\n"
        "                const Id variable{primitive_generics[IR::GenericAttributeIndex(attribute)]};\n"
        "                if (Sirit::ValidId(variable)) {\n"
        "                    module.OpStore(module.OpAccessChain(\n"
        "                                       output_f32, variable,\n"
        "                                       constant(IR::GenericAttributeElement(attribute))),\n"
        "                                   module.OpBitcast(f32_type, word));\n"
        "                }\n"
        "            } else if (attribute == IR::Attribute::Layer && Sirit::ValidId(primitive_layer)) {\n"
        "                module.OpStore(primitive_layer, module.OpBitcast(s32_type, word));\n"
        "            } else if (attribute == IR::Attribute::ViewportIndex &&\n"
        "                       Sirit::ValidId(primitive_viewport)) {\n"
        "                module.OpStore(primitive_viewport, module.OpBitcast(s32_type, word));\n"
        "            }\n"
        "        }\n"
        "    }\n"
        "    // OpenGL-style clip space, as the mesh epilogue's ConvertDepth: z = (z + w) / 2.\n",
    ),
])

patch("src/video_core/renderer_vulkan/vk_pipeline_cache.cpp", [
    (
        "// Without VK_EXT_mesh_shader: logs, once per shader set, what a skipped task/mesh pipeline would\n",
        "// " + MARK + ": generics, PrimitiveId (dropped) and, with\n"
        "// VK_EXT_shader_viewport_index_layer, Layer / ViewportIndex can be emulated.\n"
        "bool Nx2MeshPrimitiveLinesOk(const Shader::MeshInfo& mesh, const Device& device) {\n"
        "    if (mesh.primitive_lines.empty()) {\n"
        "        return true;\n"
        "    }\n"
        "    static const bool enabled{[] {\n"
        "        const char* const value{std::getenv(\"EDEN_NX2_MESH_PER_PRIMITIVE\")};\n"
        "        return value == nullptr || value[0] != '0';\n"
        "    }()};\n"
        "    if (!enabled) {\n"
        "        return false;\n"
        "    }\n"
        "    for (const u32 line : mesh.primitive_lines) {\n"
        "        const auto attribute{static_cast<Shader::IR::Attribute>(line)};\n"
        "        if (Shader::IR::IsGeneric(attribute) || attribute == Shader::IR::Attribute::PrimitiveId) {\n"
        "            continue;\n"
        "        }\n"
        "        if ((attribute == Shader::IR::Attribute::Layer ||\n"
        "             attribute == Shader::IR::Attribute::ViewportIndex) &&\n"
        "            device.IsExtShaderViewportIndexLayerSupported()) {\n"
        "            continue;\n"
        "        }\n"
        "        static std::atomic<u32> logged{};\n"
        "        if (logged++ < 16) {\n"
        "            LOG_WARNING(Render_Vulkan,\n"
        "                        \"NX2 mesh emulation: per-primitive attribute {} is not emulated\", line);\n"
        "        }\n"
        "        return false;\n"
        "    }\n"
        "    return true;\n"
        "}\n"
        "\n"
        "// Without VK_EXT_mesh_shader: logs, once per shader set, what a skipped task/mesh pipeline would\n",
    ),
    (
        "        eligible = eligible && mesh.mesh.topology == 4 && mesh.mesh.primitive_lines.empty() &&\n",
        "        eligible = eligible && mesh.mesh.topology == 4 &&\n"
        "                   Nx2MeshPrimitiveLinesOk(mesh.mesh, device) &&\n",
    ),
    (
        "        auto runtime_info{MakeRuntimeInfo(programs, key, program, previous_stage, device)};\n",
        "        auto runtime_info{MakeRuntimeInfo(programs, key, program, previous_stage, device)};\n"
        "        if (mesh_emulation && program.stage == Shader::Stage::Fragment) {\n"
        "            // " + MARK + ": per-primitive generics arrive flat.\n"
        "            runtime_info.flat_generics = runtime_info.per_primitive_generics;\n"
        "            runtime_info.per_primitive_generics.reset();\n"
        "        }\n",
    ),
])
print("Per-primitive mesh emulation installed")
