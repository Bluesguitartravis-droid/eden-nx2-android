#!/usr/bin/env python3
"""EDEN_NX2_HMMA_TILE=1: software HMMA through row/column tiles in workgroup memory.

Build 182's EDEN_NX2_HMMA_F16=1 removed the conversions of Ocarina of Time's tensor programs but
saved only 6-8 % (s6:e40f5efb 13.8 -> 13.0 ms, s6:03bf0ffc 15.4 -> 14.3 ms a dispatch): the
gather dominates. lds_mode 2 stages each lane's six operand words by lane, then every lane
fetches 32 single words from other lanes' slots, each at its own computed address.

With the switch, each 32-lane guest warp writes its fragments into a 16x16 A tile (row-major,
8 words a row) and a 16x8 B tile (column-major, 8 words a column), 192 words, the same space
lds_mode 2 uses; after a subgroup barrier each lane reads its two A rows and two B columns as
32 consecutive words (vectorisable loads) and does its 64 FMAs. Fragment layout of
HMMA.16816 (g = lane / 4, t = lane % 4): A reg0 (g, k 2t..2t+1), reg1 (g+8, 2t), reg2 (g, 2t+8),
reg3 (g+8, 2t+8); B reg0 (k 2t, column g), reg1 (k 2t+8, column g); C/D reg0 (g, 2t..2t+1),
reg1 (g+8, 2t..2t+1). Accumulation order and the fp16 rounding after each K=8 group are the
same as the gather; with EDEN_NX2_HMMA_F16=1 the FMAs are fp16 as there.
Usage: python3 scripts/apply-nx2-hmma-tile.py <upstream-root>  (after apply-nx2-hmma-f16.py)
"""
from pathlib import Path
import sys

path = Path(sys.argv[1]) / "src/shader_recompiler/backend/spirv/emit_spirv_tensor.cpp"
text = path.read_text()
MARK = "NX2 HMMA tiles"
if MARK in text:
    print("already patched")
    raise SystemExit(0)
if "nx2_f16_math" not in text:
    raise SystemExit("emit_spirv_tensor.cpp: apply-nx2-hmma-f16.py first")


def rep(old, new):
    global text
    if text.count(old) != 1:
        raise SystemExit(f"emit_spirv_tensor.cpp: anchor found {text.count(old)} times:\n{old}")
    text = text.replace(old, new, 1)


rep("        if (lds_mode == 2) {\n"
    "            const Id own = ctx.OpIMul(u32ty, add(group_base, lane), imm(6));\n",
    "        // " + MARK + ": EDEN_NX2_HMMA_TILE=1 (lds_mode 2 only, same workgroup memory).\n"
    "        static const bool nx2_tile_switch = [] {\n"
    "            const char* v = std::getenv(\"EDEN_NX2_HMMA_TILE\");\n"
    "            return v && v[0] == '1';\n"
    "        }();\n"
    "        const bool nx2_tile = nx2_tile_switch && lds_mode == 2;\n"
    "        if (nx2_tile) {\n"
    "            const u32 nx2_words = variant == 1 ? 8U : 4U; // K/2 words a row or column\n"
    "            const Id warp = ctx.OpShiftRightLogical(u32ty, add(group_base, lane), imm(5));\n"
    "            const Id tile = ctx.OpIMul(u32ty, warp, imm(192));\n"
    "            const Id g = and_(ctx.OpShiftRightLogical(u32ty, lane, imm(2)), 7);\n"
    "            const Id t = and_(lane, 3);\n"
    "            const auto a_word = [&](Id row, Id word) {\n"
    "                return add(tile, add(ctx.OpShiftLeftLogical(u32ty, row, imm(3)), word));\n"
    "            };\n"
    "            const auto b_word = [&](Id column, Id word) {\n"
    "                return add(add(tile, imm(128)),\n"
    "                           add(ctx.OpShiftLeftLogical(u32ty, column, imm(3)), word));\n"
    "            };\n"
    "            const Id g8 = add(g, imm(8));\n"
    "            const Id t4 = add(t, imm(4));\n"
    "            ctx.OpStore(lds_at(a_word(g, t)), inputs[0]);\n"
    "            ctx.OpStore(lds_at(a_word(g8, t)), inputs[1]);\n"
    "            ctx.OpStore(lds_at(b_word(g, t)), inputs[4]);\n"
    "            if (variant == 1) {\n"
    "                ctx.OpStore(lds_at(a_word(g, t4)), inputs[2]);\n"
    "                ctx.OpStore(lds_at(a_word(g8, t4)), inputs[3]);\n"
    "                ctx.OpStore(lds_at(b_word(g, t4)), inputs[5]);\n"
    "            }\n"
    "            lds_barrier();\n"
    "            const auto widen = [&](Id word) {\n"
    "                return nx2_f16_math ? ctx.OpBitcast(nx2_h2, word) : unpack(word);\n"
    "            };\n"
    "            std::array<std::array<Id, 8>, 2> a_rows{};\n"
    "            std::array<std::array<Id, 8>, 2> b_cols{};\n"
    "            const Id t2 = ctx.OpShiftLeftLogical(u32ty, t, imm(1));\n"
    "            for (u32 w = 0; w < nx2_words; ++w) {\n"
    "                a_rows[0][w] = widen(ctx.OpLoad(u32ty, lds_at(a_word(g, imm(w)))));\n"
    "                a_rows[1][w] = widen(ctx.OpLoad(u32ty, lds_at(a_word(g8, imm(w)))));\n"
    "                b_cols[0][w] = widen(ctx.OpLoad(u32ty, lds_at(b_word(t2, imm(w)))));\n"
    "                b_cols[1][w] =\n"
    "                    widen(ctx.OpLoad(u32ty, lds_at(b_word(add(t2, imm(1)), imm(w)))));\n"
    "            }\n"
    "            for (u32 w = 0; w < nx2_words; ++w) {\n"
    "                for (u32 row = 0; row < 2; ++row) {\n"
    "                    for (u32 col = 0; col < 2; ++col) {\n"
    "                        Id value = ctx.OpLoad(nx2_acc_ty, accumulators[row * 2 + col]);\n"
    "                        for (u32 half = 0; half < 2; ++half) {\n"
    "                            value = ctx.OpFma(\n"
    "                                nx2_acc_ty, ctx.OpCompositeExtract(nx2_acc_ty, a_rows[row][w], half),\n"
    "                                ctx.OpCompositeExtract(nx2_acc_ty, b_cols[col][w], half), value);\n"
    "                            ctx.Decorate(value, spv::Decoration::NoContraction);\n"
    "                        }\n"
    "                        ctx.OpStore(accumulators[row * 2 + col], value);\n"
    "                    }\n"
    "                }\n"
    "                // HMMA.F16 rounds after each K=8 group (words 0-3 and 4-7).\n"
    "                if (w % 4 == 3 && !nx2_f16_math) {\n"
    "                    for (const Id accumulator : accumulators)\n"
    "                        ctx.OpStore(accumulator, round_half(ctx.OpLoad(f32ty, accumulator)));\n"
    "                }\n"
    "            }\n"
    "        }\n"
    "        if (lds_mode == 2 && !nx2_tile) {\n"
    "            const Id own = ctx.OpIMul(u32ty, add(group_base, lane), imm(6));\n")
rep("        if (variant == 0) {\n"
    "            emit_group([](Id low, Id) { return low; });\n",
    "        if (nx2_tile) {\n"
    "            // " + MARK + ": computed above.\n"
    "        } else if (variant == 0) {\n"
    "            emit_group([](Id low, Id) { return low; });\n")
path.write_text(text)
print("HMMA tile switch installed (EDEN_NX2_HMMA_TILE=1)")
