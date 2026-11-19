// Emits a vertex shader that reads an object through one untracked pointer (like Ocarina's scenery
// vertex shaders) and writes the SPIR-V to argv[1]. EDEN_NX2_GMEM_ROUTE_CACHE decides the path.
#include <cstdio>
#include <fstream>
#include <vector>

#include "shader_recompiler/backend/spirv/emit_spirv.h"
#include "shader_recompiler/environment.h"
#include "shader_recompiler/frontend/ir/basic_block.h"
#include "shader_recompiler/frontend/ir/ir_emitter.h"
#include "shader_recompiler/frontend/ir/program.h"
#include "shader_recompiler/host_translate_info.h"
#include "shader_recompiler/ir_opt/passes.h"
#include "shader_recompiler/profile.h"

using namespace Shader;

class StubEnvironment final : public Environment {
public:
    StubEnvironment() { stage = Stage::VertexB; }
    u64 ReadInstruction(u32) override { return 0; }
    u32 ReadCbufValue(u32, u32) override { return 0; }
    TextureType ReadTextureType(u32) override { return {}; }
    TexturePixelFormat ReadTexturePixelFormat(u32) override { return {}; }
    bool IsTexturePixelFormatInteger(u32) override { return false; }
    u32 ReadViewportTransformState() override { return 0; }
    u32 TextureBoundBuffer() const override { return 0; }
    u32 LocalMemorySize() const override { return 0; }
    u32 SharedMemorySize() const override { return 0; }
    std::array<u32, 3> WorkgroupSize() const override { return {1, 1, 1}; }
    bool HasHLEMacroState() const override { return false; }
    std::optional<ReplaceConstant> GetReplaceConstBuffer(u32, u32) override { return std::nullopt; }
    void Dump(u64, u64) override {}
};

int main(int argc, char** argv) {
    const int blocks_mode = argc > 2 ? std::atoi(argv[2]) : 0;
    ObjectPool<IR::Inst> pool;
    ObjectPool<IR::Block> block_pool;
    IR::Block* const entry{block_pool.Create(pool)};
    IR::Block* const second{block_pool.Create(pool)};
    IR::IREmitter ir{*entry};
    const auto cbuf_pointer = [&](u32 offset) {
        return IR::U64{ir.PackUint2x32(ir.CompositeConstruct(ir.GetCbuf(ir.Imm32(0), ir.Imm32(offset)),
                                                             ir.GetCbuf(ir.Imm32(0), ir.Imm32(offset + 4))))};
    };
    // Five tracked pointers in cbuf 0 (the candidate buffers); the object's pointer is read from
    // the first, so the loads through it are untracked.
    const auto loaded = ir.LoadGlobal64(cbuf_pointer(0x110));
    IR::U32 sum{ir.Imm32(0)};
    for (u32 i = 1; i < 5; ++i) {
        sum = ir.IAdd(sum, IR::U32{ir.LoadGlobal32(cbuf_pointer(0x110 + i * 0x10))});
    }
    // Like the translator: the LDG.64 result in R4/R5, then each LDG [R4.64 + imm] packs again.
    const IR::U32 r4{ir.CompositeExtract(loaded, 0)};
    const IR::U32 r5{ir.CompositeExtract(loaded, 1)};
    const auto object = [&](IR::IREmitter& e, u64 offset) {
        return IR::U64{e.IAdd(IR::U64{e.PackUint2x32(e.CompositeConstruct(r4, r5))}, e.Imm64(offset))};
    };
    for (u32 i = 0; i < 6; ++i) {
        sum = ir.IAdd(sum, IR::U32{ir.LoadGlobal32(object(ir, i * 4))});
    }
    const auto v2 = ir.LoadGlobal64(object(ir, 0x40));
    sum = ir.IAdd(sum, IR::U32{ir.CompositeExtract(v2, 1)});
    const auto v4 = ir.LoadGlobal128(object(ir, 0x80));
    sum = ir.IAdd(sum, IR::U32{ir.CompositeExtract(v4, 3)});

    IR::Program program;
    program.stage = Stage::VertexB;
    if (blocks_mode) {
        // A second block reading through the same pointer: it must find the route again.
        IR::IREmitter ir2{*second};
        IR::U32 more{ir2.LoadGlobal32(object(ir2, 0x100))};
        more = ir2.IAdd(more, IR::U32{ir2.LoadGlobal32(object(ir2, 0x104))});
        ir2.SetAttribute(IR::Attribute::PositionY, ir2.BitCast<IR::F32>(more), ir2.Imm32(0));
        entry->AddBranch(second);
        program.blocks = {entry, second};
        program.post_order_blocks = {second, entry};
        program.syntax_list = {{{.block = entry}, IR::AbstractSyntaxNode::Type::Block},
                               {{.block = second}, IR::AbstractSyntaxNode::Type::Block},
                               {{}, IR::AbstractSyntaxNode::Type::Return}};
    } else {
        program.blocks = {entry};
        program.post_order_blocks = {entry};
        program.syntax_list = {{{.block = entry}, IR::AbstractSyntaxNode::Type::Block},
                               {{}, IR::AbstractSyntaxNode::Type::Return}};
    }
    ir.SetAttribute(IR::Attribute::PositionX, ir.BitCast<IR::F32>(sum), ir.Imm32(0));

    HostTranslateInfo host{};
    host.min_ssbo_alignment = 16;
    StubEnvironment env;
    Optimization::GlobalMemoryToStorageBufferPass(env, program, host);
    Optimization::CollectShaderInfoPass(env, program);
    program.info.native_global_bounds = true;
    program.info.constant_buffer_descriptors = {{0, 1}};
    std::fprintf(stderr, "storage buffers: %zu, uses_global_memory=%d\n",
                 program.info.storage_buffers_descriptors.size(), program.info.uses_global_memory);

    Profile profile{};
    profile.supported_spirv = 0x00010300;
    profile.support_int64 = true;
    profile.support_descriptor_aliasing = true;
    profile.unified_descriptor_binding = true;
    profile.min_ssbo_alignment = 16;
    const std::vector<u32> code{Backend::SPIRV::EmitSPIRV(profile, program)};
    std::ofstream out(argv[1], std::ios::binary);
    out.write(reinterpret_cast<const char*>(code.data()), code.size() * 4);
    std::fprintf(stderr, "wrote %zu words\n", code.size());
    return 0;
}
