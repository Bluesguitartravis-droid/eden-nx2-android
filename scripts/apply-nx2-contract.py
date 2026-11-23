#!/usr/bin/env python3
"""EDEN_NX2_CONTRACT=fs|all and EDEN_NX2_NO_PRESERVE=fs|all (read at start): let the driver fuse multiplies and adds (no SPIR-V
NoContraction) in fragment shaders (fs) or in every stage (all).

Eden marks every float add and multiply translated from a separate Maxwell FADD/FMUL with
NoContraction, so the result matches NVIDIA bit for bit. In Ocarina's costliest fragment shader
(d9ba002d, ~5 ms a frame) 2,064 of its 2,099 float adds and multiplies carry it, so Turnip can't
turn mul+add pairs into ir3's mad. Fragment results may differ in the last bit; vertex positions
could then differ between passes that must match (depth prepass), hence fs first.

EDEN_NX2_NO_PRESERVE leaves off the fp16/fp32 SignedZeroInfNanPreserve execution modes too: Mesa
also keeps mul+add apart (and skips other float folds) for ops that must preserve signed zeros,
infinities and NaNs, which every op of these shaders does.

The pipelines' driver binaries are stored under a salted key while this is on, so binaries built
from the decorated SPIR-V are not reused (the first run with it rebuilds the pipelines)."""
from pathlib import Path
import sys

src = Path(sys.argv[1]).resolve() / "src"


def replace(path, old, new):
    text = path.read_text()
    if text.count(old) != 1:
        sys.exit(f"contract: {path.name}: expected 1 match, found {text.count(old)}:\n{old}")
    path.write_text(text.replace(old, new))


(src / "shader_recompiler/nx2_contract.h").write_text('''// SPDX-License-Identifier: GPL-3.0-or-later
// EDEN_NX2_CONTRACT and EDEN_NX2_NO_PRESERVE (scripts/apply-nx2-contract.py)
#pragma once

#include <cstdlib>
#include <cstring>

#include "common/common_types.h"
#include "shader_recompiler/stage.h"

namespace Shader {

/// fs -> 1 (fragment shaders), all -> 2 (every stage), else 0
inline int Nx2StageMode(const char* name) {
    const char* const value = std::getenv(name);
    if (value == nullptr) {
        return 0;
    }
    if (std::strcmp(value, "fs") == 0) {
        return 1;
    }
    return std::strcmp(value, "all") == 0 ? 2 : 0;
}

inline int Nx2ContractMode() {
    static const int mode = Nx2StageMode("EDEN_NX2_CONTRACT");
    return mode;
}

inline int Nx2NoPreserveMode() {
    static const int mode = Nx2StageMode("EDEN_NX2_NO_PRESERVE");
    return mode;
}

inline bool Nx2StageApplies(int mode, Stage stage) {
    return mode == 2 || (mode == 1 && stage == Stage::Fragment);
}

/// Whether NoContraction is left off the float ops of a program of `stage`
inline bool Nx2Contracts(Stage stage) {
    return Nx2StageApplies(Nx2ContractMode(), stage);
}

/// Whether the fp16/fp32 SignedZeroInfNanPreserve execution modes are left off
inline bool Nx2DropsPreserve(Stage stage) {
    return Nx2StageApplies(Nx2NoPreserveMode(), stage);
}

/// XORed into pipeline binary keys, so binaries built without the change are not reused
inline u64 Nx2ContractGraphicsSalt() {
    const u64 modes = static_cast<u64>(Nx2ContractMode()) | (static_cast<u64>(Nx2NoPreserveMode()) << 4);
    return modes == 0 ? 0 : 0x6e78325f63746100ULL + modes;
}

inline u64 Nx2ContractComputeSalt() {
    const u64 modes = static_cast<u64>(Nx2ContractMode() == 2) |
                      (static_cast<u64>(Nx2NoPreserveMode() == 2) << 4);
    return modes == 0 ? 0 : 0x6e78325f63746300ULL + modes;
}

} // namespace Shader
''')

fp = src / "shader_recompiler/backend/spirv/emit_spirv_floating_point.cpp"
replace(fp, '#include "shader_recompiler/frontend/ir/modifiers.h"\n',
        '#include "shader_recompiler/frontend/ir/modifiers.h"\n#include "shader_recompiler/nx2_contract.h"\n')
replace(fp, """    if (flags.no_contraction) {
        ctx.Decorate(op, spv::Decoration::NoContraction);""", """    if (flags.no_contraction && !Nx2Contracts(ctx.stage)) {
        ctx.Decorate(op, spv::Decoration::NoContraction);""")

es = src / "shader_recompiler/backend/spirv/emit_spirv.cpp"
replace(es, '#include "shader_recompiler/backend/spirv/emit_spirv.h"\n',
        '#include "shader_recompiler/backend/spirv/emit_spirv.h"\n#include "shader_recompiler/nx2_contract.h"\n')
replace(es, """    if (profile.has_broken_fp16_float_controls && program.info.uses_fp16) {
        return;
    }
    if (program.info.uses_fp16 && profile.support_fp16_signed_zero_nan_preserve) {""", """    if (profile.has_broken_fp16_float_controls && program.info.uses_fp16) {
        return;
    }
    if (Nx2DropsPreserve(program.stage)) {
        return;
    }
    if (program.info.uses_fp16 && profile.support_fp16_signed_zero_nan_preserve) {""")

gp = src / "video_core/renderer_vulkan/vk_graphics_pipeline.cpp"
replace(gp, '#include "video_core/renderer_vulkan/vk_pipeline_binaries.h"\n',
        '#include "video_core/renderer_vulkan/vk_pipeline_binaries.h"\n#include "shader_recompiler/nx2_contract.h"\n')
replace(gp, """    pipeline = binaries ? binaries->CreateGraphicsPipeline(pipeline_ci, *pipeline_cache,
                                                           GraphicsRecordKey(key))""",
        """    pipeline = binaries ? binaries->CreateGraphicsPipeline(pipeline_ci, *pipeline_cache,
                                                           GraphicsRecordKey(key) ^
                                                               Shader::Nx2ContractGraphicsSalt())""")
replace(gp, """                                  GraphicsRecordKey(key) ^ (program * 0x9e3779b97f4a7c15ULL))""",
        """                                  GraphicsRecordKey(key) ^ (program * 0x9e3779b97f4a7c15ULL) ^
                                      Shader::Nx2ContractGraphicsSalt())""")

cp = src / "video_core/renderer_vulkan/vk_compute_pipeline.cpp"
replace(cp, '#include "video_core/renderer_vulkan/vk_pipeline_binaries.h"\n',
        '#include "video_core/renderer_vulkan/vk_pipeline_binaries.h"\n#include "shader_recompiler/nx2_contract.h"\n')
replace(cp, """            pipeline = binaries ? binaries->CreateComputePipeline(compute_ci, *pipeline_cache,
                                                                  binary_key)""",
        """            pipeline = binaries ? binaries->CreateComputePipeline(
                                      compute_ci, *pipeline_cache,
                                      binary_key ^ Shader::Nx2ContractComputeSalt())""")
print("contract: EDEN_NX2_CONTRACT added")
