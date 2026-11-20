#!/usr/bin/env python3
"""EDEN_NX2_RELAXED_FS=1: fragment shader float math decorated RelaxedPrecision (fp16 on Adreno).

Build 212, Ocarina at Lon Lon Ranch at night: the frame is GPU-bound in a few heavy draws. The
costliest, s1:b809b6c2+s5:d9ba002d (5 draws, ~9 ms at 854x480), has a 3,223-instruction fragment
shader on Turnip using 48 full registers and no half registers, so only 4 waves fit per core
(NX2_PSTATS). Turnip lowers RelaxedPrecision ("mediump") math to 16 bits, which runs at twice the
rate and halves the registers.

With the switch, every 32-bit float arithmetic result (and GLSL.std.450 math) in fragment shaders
is decorated RelaxedPrecision after assembly. This is a precision trade, off by default: expect
banding or shimmer where the game needs full precision.

Usage: apply-nx2-relaxed-fragment.py <upstream source root>
"""
import pathlib
import sys

path = pathlib.Path(sys.argv[1]) / "src/shader_recompiler/backend/spirv/emit_spirv.cpp"
text = path.read_text()
if "NX2 relaxed fragment" in text:
    print("emit_spirv.cpp: already patched")
    sys.exit(0)

helper = r'''
// NX2 relaxed fragment: EDEN_NX2_RELAXED_FS=1 decorates the fragment shader's 32-bit float
// arithmetic RelaxedPrecision, which Adreno's compilers run in 16 bits.
bool Nx2RelaxedFragment() {
    static const bool enabled = [] {
        const char* value = std::getenv("EDEN_NX2_RELAXED_FS");
        return value != nullptr && value[0] == '1';
    }();
    return enabled;
}

void Nx2RelaxFragmentPrecision(std::vector<u32>& code) {
    constexpr size_t header_words = 5;
    if (code.size() <= header_words) {
        return;
    }
    std::vector<bool> float_type;
    std::vector<u32> relaxed;
    u32 glsl_set = 0;
    size_t first_type = 0;
    const auto mark_type = [&](u32 id) {
        if (id >= float_type.size()) {
            float_type.resize(static_cast<size_t>(id) + 1);
        }
        float_type[id] = true;
    };
    const auto is_float = [&](u32 id) { return id < float_type.size() && float_type[id]; };
    for (size_t pc = header_words; pc < code.size();) {
        const u32 count = code[pc] >> 16;
        const u32 op = code[pc] & 0xffff;
        if (count == 0 || pc + count > code.size()) {
            return; // Malformed: leave the module as it is
        }
        const u32* words = &code[pc];
        if (first_type == 0 && op >= 19 && op <= 39) {
            first_type = pc;
        }
        switch (op) {
        case 11: // OpExtInstImport
            if (count >= 3 && std::memcmp(&words[2], "GLSL.std.450", 12) == 0) {
                glsl_set = words[1];
            }
            break;
        case 22: // OpTypeFloat
            if (count >= 3 && words[2] == 32) {
                mark_type(words[1]);
            }
            break;
        case 23: // OpTypeVector
            if (count >= 4 && is_float(words[2])) {
                mark_type(words[1]);
            }
            break;
        case 127: // OpFNegate
        case 129: // OpFAdd
        case 131: // OpFSub
        case 133: // OpFMul
        case 136: // OpFDiv
        case 141: // OpFMod
        case 142: // OpVectorTimesScalar
        case 148: // OpDot
            if (count >= 3 && is_float(words[1])) {
                relaxed.push_back(words[2]);
            }
            break;
        case 12: // OpExtInst
            if (count >= 5 && glsl_set != 0 && words[3] == glsl_set && is_float(words[1])) {
                relaxed.push_back(words[2]);
            }
            break;
        default:
            break;
        }
        pc += count;
    }
    if (relaxed.empty() || first_type == 0) {
        return;
    }
    std::vector<u32> decorations;
    decorations.reserve(relaxed.size() * 3);
    for (const u32 id : relaxed) {
        decorations.push_back((3u << 16) | 71u); // OpDecorate
        decorations.push_back(id);
        decorations.push_back(0u); // RelaxedPrecision
    }
    code.insert(code.begin() + static_cast<std::ptrdiff_t>(first_type), decorations.begin(),
                decorations.end());
}
'''

anchor = "void RewriteOpcodes(std::vector<u32>& code, std::span<const std::pair<u32, spv::Op>> rewrites) {"
if text.count(anchor) != 1:
    sys.exit("emit_spirv.cpp: RewriteOpcodes anchor not found")
text = text.replace(anchor, helper + "\n" + anchor)

call_anchor = """    std::vector<u32> code{ctx.Assemble()};
    RewriteOpcodes(code, ctx.opcode_rewrites);
"""
if text.count(call_anchor) != 1:
    sys.exit("emit_spirv.cpp: Assemble anchor not found")
text = text.replace(call_anchor, call_anchor + """    if (program.stage == Stage::Fragment && Nx2RelaxedFragment()) {
        Nx2RelaxFragmentPrecision(code);
    }
""")
if "#include <cstring>" not in text:
    text = text.replace("#include <algorithm>\n", "#include <algorithm>\n#include <cstdlib>\n#include <cstring>\n", 1)
path.write_text(text)
print("emit_spirv.cpp: EDEN_NX2_RELAXED_FS fragment precision relaxing")
