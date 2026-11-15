#!/usr/bin/env python3
"""EDEN_NX2_HMMA_F16=1: software HMMA (tensor-core emulation) in native fp16 arithmetic.

Build 181's logs of Ocarina of Time's slowest compute programs (s6:e40f5efb, s6:03bf0ffc,
s6:9302bc0d, 13-17 ms of a 44-50 ms GPU frame together): 176-192 TensorMulAdd (HMMA.16816.F16)
each, and Turnip's statistics give 41-46k instructions of which 20-22k are conversions (COV),
48 registers, 4 waves per core, no half registers. The software HMMA widens every fp16 operand
fetched from another lane to fp32 (OpUnpackHalf2x16), does fp32 FMAs and rounds the four
accumulators to fp16 after each K=8 group: about 80 conversions per HMMA.

With the switch, operands are bitcast to f16vec2 and the FMAs are fp16 (half registers, no
conversions). The difference: fp16 rounds after every FMA instead of after each group of eight,
a few fp16 ULPs on a neural network's activations. Off by default until a test shows whether the
picture stays the same and how much GPU time it saves.
Usage: python3 scripts/apply-nx2-hmma-f16.py <upstream-root>
"""
from pathlib import Path
import sys

path = Path(sys.argv[1]) / "src/shader_recompiler/backend/spirv/emit_spirv_tensor.cpp"
text = path.read_text()
MARK = "NX2 HMMA fp16 math"
if MARK in text:
    print("already patched")
    raise SystemExit(0)


def rep(old, new, count=1):
    global text
    if text.count(old) != count:
        raise SystemExit(f"emit_spirv_tensor.cpp: anchor found {text.count(old)} times:\n{old}")
    text = text.replace(old, new)


rep("        const Id f16ty = ctx.native_half_rounding ? ctx.TypeFloat(16) : Id{};\n",
    "        const Id f16ty = ctx.native_half_rounding ? ctx.TypeFloat(16) : Id{};\n"
    "        // " + MARK + ": EDEN_NX2_HMMA_F16=1 multiplies and accumulates in fp16.\n"
    "        static const bool nx2_f16_math = [] {\n"
    "            const char* v = std::getenv(\"EDEN_NX2_HMMA_F16\");\n"
    "            return v && v[0] == '1';\n"
    "        }();\n"
    "        const Id nx2_h = nx2_f16_math ? ctx.TypeFloat(16) : Id{};\n"
    "        const Id nx2_h2 = nx2_f16_math ? ctx.TypeVector(nx2_h, 2) : Id{};\n"
    "        if (nx2_f16_math) {\n"
    "            ctx.AddCapability(spv::Capability::Float16);\n"
    "        }\n"
    "        const Id nx2_acc_ty = nx2_f16_math ? nx2_h : f32ty;\n")

rep("        const auto unpack = [&](Id v) { return ctx.OpUnpackHalf2x16(ctx.F32[2], v); };\n",
    "        const auto unpack = [&](Id v) { return ctx.OpUnpackHalf2x16(ctx.F32[2], v); };\n")

# The operand unpack used for C and the fetched A/B words.
rep("            acc = ctx.AddLocalVariable(ctx.TypePointer(spv::StorageClass::Function, f32ty),\n",
    "            acc = ctx.AddLocalVariable(ctx.TypePointer(spv::StorageClass::Function, nx2_acc_ty),\n")
rep("            const Id c = unpack(inputs[6 + row]);\n"
    "            for (u32 col = 0; col < 2; ++col)\n"
    "                ctx.OpStore(accumulators[row * 2 + col], ctx.OpCompositeExtract(f32ty, c, col));\n",
    "            const Id c = nx2_f16_math ? ctx.OpBitcast(nx2_h2, inputs[6 + row])\n"
    "                                      : unpack(inputs[6 + row]);\n"
    "            for (u32 col = 0; col < 2; ++col)\n"
    "                ctx.OpStore(accumulators[row * 2 + col],\n"
    "                            ctx.OpCompositeExtract(nx2_acc_ty, c, col));\n")
rep("                    bs[col] = unpack(fetch(b, add(b_base, imm(component + col * 4)),\n"
    "                                           pick(imm(4), imm(5))));\n",
    "                {\n"
    "                    const Id word = fetch(b, add(b_base, imm(component + col * 4)),\n"
    "                                          pick(imm(4), imm(5)));\n"
    "                    bs[col] = nx2_f16_math ? ctx.OpBitcast(nx2_h2, word) : unpack(word);\n"
    "                }\n")
rep("                    const Id as = unpack(fetch(pick(inputs[row], inputs[row + 2]),\n"
    "                                               add(a_base, imm(component)),\n"
    "                                               pick(imm(row), imm(row + 2))));\n",
    "                    const Id a_word = fetch(pick(inputs[row], inputs[row + 2]),\n"
    "                                            add(a_base, imm(component)),\n"
    "                                            pick(imm(row), imm(row + 2)));\n"
    "                    const Id as = nx2_f16_math ? ctx.OpBitcast(nx2_h2, a_word) : unpack(a_word);\n")
rep("                        Id value = ctx.OpLoad(f32ty, accumulators[row * 2 + col]);\n"
    "                        for (u32 half = 0; half < 2; ++half) {\n"
    "                            value = ctx.OpFma(f32ty, ctx.OpCompositeExtract(f32ty, as, half),\n"
    "                                              ctx.OpCompositeExtract(f32ty, bs[col], half), value);\n",
    "                        Id value = ctx.OpLoad(nx2_acc_ty, accumulators[row * 2 + col]);\n"
    "                        for (u32 half = 0; half < 2; ++half) {\n"
    "                            value = ctx.OpFma(nx2_acc_ty,\n"
    "                                              ctx.OpCompositeExtract(nx2_acc_ty, as, half),\n"
    "                                              ctx.OpCompositeExtract(nx2_acc_ty, bs[col], half),\n"
    "                                              value);\n")
rep("            for (const Id accumulator : accumulators)\n"
    "                ctx.OpStore(accumulator, round_half(ctx.OpLoad(f32ty, accumulator)));\n",
    "            if (!nx2_f16_math) {\n"
    "                for (const Id accumulator : accumulators)\n"
    "                    ctx.OpStore(accumulator, round_half(ctx.OpLoad(f32ty, accumulator)));\n"
    "            }\n")
rep("        std::array<Id, 2> packed;\n"
    "        for (u32 row = 0; row < 2; ++row)\n"
    "            packed[row] = ctx.OpPackHalf2x16(u32ty, ctx.OpCompositeConstruct(ctx.F32[2],\n"
    "                ctx.OpLoad(f32ty, accumulators[row * 2]), ctx.OpLoad(f32ty, accumulators[row * 2 + 1])));\n",
    "        std::array<Id, 2> packed;\n"
    "        for (u32 row = 0; row < 2; ++row) {\n"
    "            if (nx2_f16_math) {\n"
    "                packed[row] = ctx.OpBitcast(u32ty, ctx.OpCompositeConstruct(nx2_h2,\n"
    "                    ctx.OpLoad(nx2_h, accumulators[row * 2]),\n"
    "                    ctx.OpLoad(nx2_h, accumulators[row * 2 + 1])));\n"
    "                continue;\n"
    "            }\n"
    "            packed[row] = ctx.OpPackHalf2x16(u32ty, ctx.OpCompositeConstruct(ctx.F32[2],\n"
    "                ctx.OpLoad(f32ty, accumulators[row * 2]), ctx.OpLoad(f32ty, accumulators[row * 2 + 1])));\n"
    "        }\n")
path.write_text(text)
print("HMMA fp16 math switch installed (EDEN_NX2_HMMA_F16=1)")
