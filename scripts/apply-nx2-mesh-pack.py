#!/usr/bin/env python3
"""Mesh emulation: two 32-thread mesh workgroups per Vulkan workgroup (build 286).

Without VK_EXT_mesh_shader each mesh workgroup (32 invocations in Ocarina's foliage) ran as its own
Vulkan compute workgroup; Adreno waves are 64/128 lanes, so at least half of each wave idled. For
a mesh program without a task program that writes no memory and has no shared memory and no
barrier, early return or epilogue inside a branch (scripts/apply-nx2-mesh-pack-check.py: all of
Ocarina's non-task ones), the compute shader now has LocalSize 64: invocations 0-31 run mesh
workgroup 2x and 32-63 run 2x+1, each half with its own copy of the emulation's workgroup
arrays (attribute and map regions, primitive count, payload). Guest warps stay apart: Eden's warp
emulation already splits a host subgroup into 32-lane partitions (lane & 31, ballots by lane >> 5).

The odd last workgroup's second half would run one group past the draw: it takes the last group's
index instead (the draw's group count is the output buffer's length over the block size), runs
the same program on the same input and writes the same words as the first half. Hence no memory
writes allowed in the program.

The host dispatches ceil(groups / 2) workgroups. Off by default since build 287 (no gain measured);
EDEN_NX2_MESH_PACK=1 (read at app start) turns it on. The pipeline build logs "Mesh pack: N of M emulated mesh
programs packed" every 16 programs.
"""
from pathlib import Path
import sys

src = Path(sys.argv[1]).resolve() / "src"


def patch(rel, old, new, count=1):
    path = src / rel
    text = path.read_text()
    found = text.count(old)
    if found != count:
        sys.exit(f"mesh-pack: {rel}: expected {count} match(es), found {found}:\n{old}")
    path.write_text(text.replace(old, new))


# --- RuntimeInfo ------------------------------------------------------------------------------
patch("shader_recompiler/runtime_info.h", '''    u32 mesh_task_payload_stride{}; ///< payload buffer words per task workgroup
};''', '''    u32 mesh_task_payload_stride{}; ///< payload buffer words per task workgroup
    /// Mesh emulation without a task: mesh workgroups per compute workgroup (1 or 2,
    /// scripts/apply-nx2-mesh-pack.py).
    u32 mesh_pack{1};
};''')

# --- Emit context -----------------------------------------------------------------------------
patch("shader_recompiler/backend/spirv/spirv_emit_context.h", '''    Id mesh_group_index{};
    /// NumWorkgroups of a native mesh program''', '''    Id mesh_group_index{};
    /// Packed mesh emulation (RuntimeInfo::mesh_pack == 2): this half's mesh workgroup index.
    Id mesh_packed_group{};
    u32 mesh_pack{1};
    /// NumWorkgroups of a native mesh program''')

patch("shader_recompiler/backend/spirv/spirv_emit_context.cpp", '''    mesh_info = program.mesh;
    mesh_threads = program.workgroup_size[0];''', '''    mesh_info = program.mesh;
    mesh_threads = program.workgroup_size[0];
    // Packed emulation: each half of the workgroup has its own copy of the arrays below.
    mesh_pack = (as_compute && stage == Stage::Mesh && runtime_info.mesh_pack == 2 &&
                 runtime_info.mesh_task_payload_binding == ~0U)
                    ? 2U
                    : 1U;''')
patch("shader_recompiler/backend/spirv/spirv_emit_context.cpp", '''    const Id payload_type{TypeArray(U32[1], Const((std::max)(mesh_info.payload_words, 1U)))};''',
      '''    const Id payload_type{
        TypeArray(U32[1], Const((std::max)(mesh_info.payload_words, 1U) * mesh_pack))};''')
patch("shader_recompiler/backend/spirv/spirv_emit_context.cpp", '''    mesh_attributes = DefineVariable(*this, TypeArray(U32[1], Const(mesh_attribute_words)),
                                     std::nullopt, spv::StorageClass::Workgroup);
    Name(mesh_attributes, "isbe_attributes");
    mesh_map = DefineVariable(*this, TypeArray(U32[1], Const(mesh_map_bytes)), std::nullopt,
                              spv::StorageClass::Workgroup);
    Name(mesh_map, "isbe_map");
    mesh_primitive_count =
        DefineVariable(*this, U32[1], std::nullopt, spv::StorageClass::Workgroup);
    Name(mesh_primitive_count, "isbe_primitive_count");
''', '''    mesh_attributes =
        DefineVariable(*this, TypeArray(U32[1], Const(mesh_attribute_words * mesh_pack)),
                       std::nullopt, spv::StorageClass::Workgroup);
    Name(mesh_attributes, "isbe_attributes");
    mesh_map = DefineVariable(*this, TypeArray(U32[1], Const(mesh_map_bytes * mesh_pack)),
                              std::nullopt, spv::StorageClass::Workgroup);
    Name(mesh_map, "isbe_map");
    mesh_primitive_count = DefineVariable(
        *this, mesh_pack == 2 ? TypeArray(U32[1], Const(2U)) : U32[1], std::nullopt,
        spv::StorageClass::Workgroup);
    Name(mesh_primitive_count, "isbe_primitive_count");
    if (mesh_pack == 2) {
        mesh_packed_group = AddGlobalVariable(TypePointer(spv::StorageClass::Private, U32[1]),
                                              spv::StorageClass::Private);
        Name(mesh_packed_group, "mesh_packed_group");
        if (profile.supported_spirv >= 0x00010400) {
            interfaces.push_back(mesh_packed_group);
        }
    }
''')

patch("shader_recompiler/backend/spirv/emit_spirv.cpp", '''            // Mesh emulation: one compute workgroup per mesh workgroup.
            execution_model = spv::ExecutionModel::GLCompute;
            ctx.AddExecutionMode(main, spv::ExecutionMode::LocalSize, program.workgroup_size[0],
                                 1U, 1U);''', '''            // Mesh emulation: one compute workgroup per mesh workgroup, or two (mesh_pack).
            execution_model = spv::ExecutionModel::GLCompute;
            ctx.AddExecutionMode(main, spv::ExecutionMode::LocalSize,
                                 program.workgroup_size[0] * ctx.mesh_pack, 1U, 1U);''')

# Guest workgroup and invocation ids.
patch("shader_recompiler/backend/spirv/emit_spirv_context_get_set.cpp", '''Id EmitWorkgroupId(EmitContext& ctx) {
    if (Sirit::ValidId(ctx.mesh_group_index)) {''', '''Id EmitWorkgroupId(EmitContext& ctx) {
    if (Sirit::ValidId(ctx.mesh_packed_group)) {
        const Id zero{ctx.u32_zero_value};
        return ctx.OpCompositeConstruct(ctx.U32[3], ctx.OpLoad(ctx.U32[1], ctx.mesh_packed_group),
                                        zero, zero);
    }
    if (Sirit::ValidId(ctx.mesh_group_index)) {''')
patch("shader_recompiler/backend/spirv/emit_spirv_context_get_set.cpp", '''Id EmitLocalInvocationId(EmitContext& ctx) {
    return ctx.OpLoad(ctx.U32[3], ctx.local_invocation_id);
}''', '''Id EmitLocalInvocationId(EmitContext& ctx) {
    const Id id{ctx.OpLoad(ctx.U32[3], ctx.local_invocation_id)};
    if (ctx.mesh_pack == 2) {
        // Packed mesh emulation: each half is its own 32-thread mesh workgroup.
        return ctx.OpCompositeInsert(
            ctx.U32[3],
            ctx.OpBitwiseAnd(ctx.U32[1], ctx.OpCompositeExtract(ctx.U32[1], id, 0U),
                             ctx.Const(31U)),
            id, 0U);
    }
    return id;
}''')

# --- Mesh emitter -----------------------------------------------------------------------------
mesh = "shader_recompiler/backend/spirv/emit_spirv_mesh.cpp"
patch(mesh, '''Id LocalInvocation(EmitContext& ctx) {
    return ctx.OpCompositeExtract(ctx.U32[1], ctx.OpLoad(ctx.U32[3], ctx.local_invocation_id), 0U);
}''', '''Id RawLocalInvocation(EmitContext& ctx) {
    return ctx.OpCompositeExtract(ctx.U32[1], ctx.OpLoad(ctx.U32[3], ctx.local_invocation_id), 0U);
}

// The invocation within its mesh workgroup (the half's, when packed).
Id LocalInvocation(EmitContext& ctx) {
    const Id raw{RawLocalInvocation(ctx)};
    return ctx.mesh_pack == 2 ? ctx.OpBitwiseAnd(ctx.U32[1], raw, ctx.Const(31U)) : raw;
}

// Packed mesh emulation: 0 for invocations 0-31, 1 for 32-63.
Id MeshHalf(EmitContext& ctx) {
    return ctx.OpShiftRightLogical(ctx.U32[1], RawLocalInvocation(ctx), ctx.Const(5U));
}

// The half's copy of a workgroup array that holds `words` words per mesh workgroup.
Id HalfOffset(EmitContext& ctx, Id index, u32 words) {
    if (ctx.mesh_pack != 2) {
        return index;
    }
    return ctx.OpIAdd(ctx.U32[1], index,
                      ctx.OpIMul(ctx.U32[1], MeshHalf(ctx), ctx.Const(words)));
}

// The mesh primitive count of this workgroup (of this half, when packed).
Id PrimitiveCountPointer(EmitContext& ctx) {
    if (ctx.mesh_pack != 2) {
        return ctx.mesh_primitive_count;
    }
    return ctx.OpAccessChain(ctx.mesh_shared_u32, ctx.mesh_primitive_count, MeshHalf(ctx));
}

// The mesh workgroup's index in the draw (WorkgroupId.x, or the packed half's).
Id MeshGroupX(EmitContext& ctx) {
    if (Sirit::ValidId(ctx.mesh_packed_group)) {
        return ctx.OpLoad(ctx.U32[1], ctx.mesh_packed_group);
    }
    return ctx.OpCompositeExtract(ctx.U32[1], ctx.OpLoad(ctx.U32[3], ctx.workgroup_id), 0U);
}''')
patch(mesh, '''Id PayloadWord(EmitContext& ctx, Id index) {
    return ctx.OpAccessChain(ctx.mesh_payload_u32, ctx.mesh_payload,
                             Clamped(ctx, index, ctx.mesh_info.payload_words));
}

Id SharedWord(EmitContext& ctx, Id array, Id index) {
    return ctx.OpAccessChain(ctx.mesh_shared_u32, array, index);
}''', '''Id PayloadWord(EmitContext& ctx, Id index) {
    const u32 words{(std::max)(ctx.mesh_info.payload_words, 1U)};
    return ctx.OpAccessChain(
        ctx.mesh_payload_u32, ctx.mesh_payload,
        HalfOffset(ctx, Clamped(ctx, index, ctx.mesh_info.payload_words), words));
}

Id SharedWord(EmitContext& ctx, Id array, Id index) {
    if (ctx.mesh_pack == 2) {
        const u32 words{array.value == ctx.mesh_map.value ? ctx.mesh_map_bytes
                                                          : ctx.mesh_attribute_words};
        index = HalfOffset(ctx, index, words);
    }
    return ctx.OpAccessChain(ctx.mesh_shared_u32, array, index);
}''')
# Every read of the mesh workgroup index goes through MeshGroupX.
patch(mesh, '''    const Id workgroup{
        ctx.OpCompositeExtract(ctx.U32[1], ctx.OpLoad(ctx.U32[3], ctx.workgroup_id), 0U)};
    Id block{ctx.OpIMul(ctx.U32[1], workgroup, ctx.Const(MeshEmulationBlockWords(mesh)))};''',
      '''    const Id workgroup{MeshGroupX(ctx)};
    Id block{ctx.OpIMul(ctx.U32[1], workgroup, ctx.Const(MeshEmulationBlockWords(mesh)))};''')
patch(mesh, '''Id EmitIsbeRead(EmitContext& ctx, Id address) {
    const Id workgroup{
        ctx.OpCompositeExtract(ctx.U32[1], ctx.OpLoad(ctx.U32[3], ctx.workgroup_id), 0U)};''',
      '''Id EmitIsbeRead(EmitContext& ctx, Id address) {
    const Id workgroup{MeshGroupX(ctx)};''')
# Primitive count accesses.
patch(mesh, '''    const Id count{ctx.OpUMin(ctx.U32[1], ctx.OpLoad(ctx.U32[1], ctx.mesh_primitive_count),
                              ctx.Const(mesh.max_primitives))};
    const Id invocation{LocalInvocation(ctx)};
    const u32 threads{ctx.mesh_threads};

    // Vertex v, line l''', '''    const Id count{ctx.OpUMin(ctx.U32[1], ctx.OpLoad(ctx.U32[1], PrimitiveCountPointer(ctx)),
                              ctx.Const(mesh.max_primitives))};
    const Id invocation{LocalInvocation(ctx)};
    const u32 threads{ctx.mesh_threads};

    // Vertex v, line l''')
patch(mesh, '''    ctx.OpStore(ctx.mesh_primitive_count, value);
}''', '''    ctx.OpStore(PrimitiveCountPointer(ctx), value);
}''')
patch(mesh, '''    } else {
        // Every invocation stores the zero.
        ctx.OpStore(ctx.mesh_primitive_count, ctx.u32_zero_value);
    }''', '''    } else {
        if (Sirit::ValidId(ctx.mesh_packed_group)) {
            // Packed: this half's mesh workgroup. The odd last workgroup's second half takes the
            // last group (the output buffer holds one block per group) and repeats its writes.
            // OpArrayLength (Sirit has none): an OpCompositeExtract of the same shape, rewritten.
            const Id words{ctx.OpCompositeExtract(ctx.U32[1], ctx.mesh_output_buffer, 0U)};
            ctx.opcode_rewrites.emplace_back(words.value, spv::Op::OpArrayLength);
            const Id groups{ctx.OpUDiv(ctx.U32[1], words,
                                       ctx.Const(MeshEmulationBlockWords(ctx.mesh_info)))};
            const Id last{ctx.OpISub(ctx.U32[1], ctx.OpUMax(ctx.U32[1], groups, ctx.Const(1U)),
                                     ctx.Const(1U))};
            const Id pair{ctx.OpCompositeExtract(ctx.U32[1],
                                                 ctx.OpLoad(ctx.U32[3], ctx.workgroup_id), 0U)};
            const Id group{ctx.OpIAdd(ctx.U32[1], ctx.OpShiftLeftLogical(ctx.U32[1], pair,
                                                                         ctx.Const(1U)),
                                      MeshHalf(ctx))};
            ctx.OpStore(ctx.mesh_packed_group, ctx.OpUMin(ctx.U32[1], group, last));
        }
        // Every invocation stores the zero.
        ctx.OpStore(PrimitiveCountPointer(ctx), ctx.u32_zero_value);
    }''')

patch("shader_recompiler/backend/spirv/emit_spirv.cpp", '''        if (opcode == spv::Op::OpGroupNonUniformShuffleXor ||
            opcode == spv::Op::OpGroupNonUniformQuadBroadcast) {''', '''        if (opcode == spv::Op::OpGroupNonUniformShuffleXor ||
            opcode == spv::Op::OpGroupNonUniformQuadBroadcast ||
            opcode == spv::Op::OpCompositeExtract) {''')

# --- Host: setup, eligibility, dispatch ---------------------------------------------------------
patch("video_core/renderer_vulkan/vk_graphics_pipeline.h", '''    std::vector<u32> distribution_spirv;
    // Output layout, for the readback diagnostics.''', '''    std::vector<u32> distribution_spirv;
    /// Mesh workgroups per compute workgroup (scripts/apply-nx2-mesh-pack.py); 1 with a task.
    u32 pack{1};
    // Output layout, for the readback diagnostics.''')

patch("video_core/renderer_vulkan/vk_graphics_pipeline.cpp", '''            if (!has_task) {
                cmdbuf.BindPipeline(VK_PIPELINE_BIND_POINT_COMPUTE, *mesh_compute_pipeline);
                bind_program_state();
                cmdbuf.Dispatch(groups, 1, 1);''', '''            if (!has_task) {
                cmdbuf.BindPipeline(VK_PIPELINE_BIND_POINT_COMPUTE, *mesh_compute_pipeline);
                bind_program_state();
                const u32 pack{(std::max)(mesh_emulation->pack, 1U)};
                cmdbuf.Dispatch((groups + pack - 1) / pack, 1, 1);''')

cache = "video_core/renderer_vulkan/vk_pipeline_cache.cpp"
patch(cache, '''        // Mesh packing check (scripts/apply-nx2-mesh-pack-check.py): log only.''', '''        // Mesh packing (scripts/apply-nx2-mesh-pack.py): two mesh workgroups per compute
        // workgroup for a program without a task, shared memory or memory writes, whose
        // barriers, epilogue and returns are all at the top level.
        {
            static const bool pack_enabled{[] {
                // Off by default since 287: no host GPU time saved at the field (Nov 27).
                const char* const value{std::getenv("EDEN_NX2_MESH_PACK")};
                return value != nullptr && std::string_view{value} == "1";
            }()};
            bool packable{pack_enabled && !has_task && mesh.workgroup_size[0] == 32 &&
                          mesh.workgroup_size[1] == 1 && mesh.workgroup_size[2] == 1 &&
                          mesh.shared_memory_size == 0};
            u32 depth{};
            for (const Shader::IR::AbstractSyntaxNode& node : mesh.syntax_list) {
                if (!packable) {
                    break;
                }
                using NodeType = Shader::IR::AbstractSyntaxNode::Type;
                switch (node.type) {
                case NodeType::If:
                case NodeType::Loop:
                    ++depth;
                    break;
                case NodeType::EndIf:
                case NodeType::Repeat:
                    depth = depth > 0 ? depth - 1 : 0;
                    break;
                case NodeType::Return:
                    packable = depth == 0;
                    break;
                case NodeType::Block:
                    for (const Shader::IR::Inst& inst : node.data.block->Instructions()) {
                        const Shader::IR::Opcode op{inst.GetOpcode()};
                        const std::string_view name{Shader::IR::NameOf(op)};
                        if (op == Shader::IR::Opcode::Barrier ||
                            op == Shader::IR::Opcode::BarrierVoteInit ||
                            op == Shader::IR::Opcode::BarrierVote ||
                            op == Shader::IR::Opcode::Epilogue) {
                            packable = packable && depth == 0;
                        } else if (name.starts_with("WriteGlobal") ||
                                   name.starts_with("GlobalAtomic") ||
                                   name.starts_with("WriteStorage") ||
                                   name.starts_with("StorageAtomic") ||
                                   name.starts_with("WriteShared") ||
                                   name.starts_with("SharedAtomic") ||
                                   (name.starts_with("Image") &&
                                    (name.find("Write") != std::string_view::npos ||
                                     name.find("Atomic") != std::string_view::npos))) {
                            packable = false;
                        }
                    }
                    break;
                default:
                    break;
                }
            }
            mesh_emulation->pack = packable ? 2U : 1U;
            static std::atomic<u32> pack_programs{};
            static std::atomic<u32> packed_programs{};
            const u32 total{pack_programs.fetch_add(1) + 1};
            const u32 packed{packable ? packed_programs.fetch_add(1) + 1 : packed_programs.load()};
            if (total % 16 == 0) {
                LOG_INFO(Render_Vulkan,
                         "Mesh pack: {} of {} emulated mesh programs packed two workgroups to "
                         "one (EDEN_NX2_MESH_PACK=1 turns it on)",
                         packed, total);
            }
        }
        // Mesh packing check (scripts/apply-nx2-mesh-pack-check.py): log only.''')
patch(cache, '''            runtime_info.mesh_as_compute = true;
            runtime_info.mesh_output_binding = mesh_emulation->output_binding;''', '''            runtime_info.mesh_as_compute = true;
            runtime_info.mesh_output_binding = mesh_emulation->output_binding;
            runtime_info.mesh_pack =
                program.stage == Shader::Stage::Mesh ? mesh_emulation->pack : 1U;''')
print("mesh pack applied")
