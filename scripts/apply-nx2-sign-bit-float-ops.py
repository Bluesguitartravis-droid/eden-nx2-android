#!/usr/bin/env python3
"""Sign-bit integer ops feeding float math become float negate/abs (free source modifiers on Adreno).

Build 214, Ocarina at Lon Lon Ranch: the fragment shader of the costliest draw
(s5:d9ba002d300ea7f9, 3,223 instructions on Turnip) has 341 `x ^ 0x80000000` on raw register bits,
each bitcast to float and fed to an add or fma (the guest flips the sign with an integer LOP).
Adreno folds a float negate into the consuming ALU instruction for free, but not an integer xor:
every one is an extra instruction per pixel.

A pass after constant propagation rewrites BitCastF32U32(BitwiseXor32(x, 0x80000000)) to
FPNeg32(BitCastF32U32(x)) and BitCastF32U32(BitwiseAnd32(x, 0x7fffffff)) to FPAbs32(...); the
integer op stays for any other user and dead code elimination removes it otherwise. The bits are
the same (negate and abs only touch the sign bit). EDEN_NX2_SIGN_FLOAT_OPS=0 turns it off.

Usage: apply-nx2-sign-bit-float-ops.py <upstream source root>
"""
import pathlib
import sys

path = pathlib.Path(sys.argv[1]) / "src/shader_recompiler/frontend/maxwell/translate_program.cpp"
text = path.read_text()
if "NX2 sign bit float ops" in text:
    print("translate_program.cpp: already patched")
    sys.exit(0)

helper = r'''// NX2 sign bit float ops: the guest's integer sign flips/clears of a float, read as a float,
// become float negate/abs, which Adreno folds into the consuming instruction.
void Nx2SignBitFloatOps(IR::Program& program) {
    static const bool enabled{[] {
        const char* const value{std::getenv("EDEN_NX2_SIGN_FLOAT_OPS")};
        return value == nullptr || value[0] != '0';
    }()};
    if (!enabled) {
        return;
    }
    for (IR::Block* const block : program.blocks) {
        for (IR::Inst& inst : block->Instructions()) {
            if (inst.GetOpcode() != IR::Opcode::BitCastF32U32) {
                continue;
            }
            const IR::Value arg{inst.Arg(0)};
            if (arg.IsImmediate()) {
                continue;
            }
            IR::Inst* const op{arg.InstRecursive()};
            const IR::Opcode code{op->GetOpcode()};
            const bool is_xor{code == IR::Opcode::BitwiseXor32};
            if (!is_xor && code != IR::Opcode::BitwiseAnd32) {
                continue;
            }
            const u32 mask{is_xor ? 0x80000000U : 0x7fffffffU};
            IR::Value source;
            if (op->Arg(1).IsImmediate() && op->Arg(1).U32() == mask) {
                source = op->Arg(0);
            } else if (op->Arg(0).IsImmediate() && op->Arg(0).U32() == mask) {
                source = op->Arg(1);
            } else {
                continue;
            }
            IR::IREmitter ir{*block, IR::Block::InstructionList::s_iterator_to(inst)};
            const IR::F32 value{ir.BitCast<IR::F32>(IR::U32{source})};
            const IR::F32 result{is_xor ? IR::F32{ir.FPNeg(value)} : IR::F32{ir.FPAbs(value)}};
            inst.ReplaceUsesWith(result);
        }
    }
}

'''

anchor = "IR::Program TranslateProgram(ObjectPool<IR::Inst>& inst_pool, ObjectPool<IR::Block>& block_pool,"
if text.count(anchor) != 1:
    sys.exit("translate_program.cpp: TranslateProgram anchor not found")
text = text.replace(anchor, helper + anchor)

call = """    Optimization::ConstantPropagationPass(env, program);
    if (!normalized_host_info.support_conditional_barrier) {"""
if text.count(call) != 1:
    sys.exit(f"translate_program.cpp: constant propagation anchor found {text.count(call)} times")
text = text.replace(call, """    Optimization::ConstantPropagationPass(env, program);
    Nx2SignBitFloatOps(program);
    if (!normalized_host_info.support_conditional_barrier) {""")
if "#include <cstdlib>" not in text:
    text = text.replace("#include <algorithm>\n", "#include <algorithm>\n#include <cstdlib>\n", 1)
path.write_text(text)
print("translate_program.cpp: sign-bit integer ops read as floats become FPNeg/FPAbs")
