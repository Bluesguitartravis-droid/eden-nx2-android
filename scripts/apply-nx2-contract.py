#!/usr/bin/env python3
"""EDEN_NX2_CONTRACT=fs|all (read at start): let the driver fuse multiplies and adds (no SPIR-V
NoContraction) in fragment shaders (fs) or in every stage (all).

Eden marks every float add and multiply translated from a separate Maxwell FADD/FMUL with
NoContraction, so the result matches NVIDIA bit for bit. In Ocarina's costliest fragment shader
(d9ba002d, ~5 ms a frame) 2,064 of its 2,099 float adds and multiplies carry it, so Turnip can't
turn mul+add pairs into ir3's mad. Fragment results may differ in the last bit; vertex positions
could then differ between passes that must match (depth prepass), hence fs first.

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
// EDEN_NX2_CONTRACT (scripts/apply-nx2-contract.py)
#pragma once

#include <cstdlib>
#include <cstring>

#include "common/common_types.h"
#include "shader_recompiler/stage.h"

namespace Shader {

/// 0 off, 1 fragment shaders, 2 every stage
inline int Nx2ContractMode() {
    static const int mode = [] {
        const char* const value = std::getenv("EDEN_NX2_CONTRACT");
        if (value == nullptr) {
            return 0;
        }
        if (std::strcmp(value, "fs") == 0) {
            return 1;
        }
        return std::strcmp(value, "all") == 0 ? 2 : 0;
    }();
    return mode;
}

/// Whether NoContraction is left off the float ops of a program of `stage`
inline bool Nx2Contracts(Stage stage) {
    const int mode = Nx2ContractMode();
    return mode == 2 || (mode == 1 && stage == Stage::Fragment);
}

/// XORed into pipeline binary keys, so binaries built without the change are not reused
inline u64 Nx2ContractGraphicsSalt() {
    return Nx2ContractMode() == 0 ? 0 : 0x6e78325f63746100ULL + static_cast<u64>(Nx2ContractMode());
}

inline u64 Nx2ContractComputeSalt() {
    return Nx2ContractMode() == 2 ? 0x6e78325f63746300ULL : 0;
}

} // namespace Shader
''')

fp = src / "shader_recompiler/backend/spirv/emit_spirv_floating_point.cpp"
replace(fp, '#include "shader_recompiler/frontend/ir/modifiers.h"\n',
        '#include "shader_recompiler/frontend/ir/modifiers.h"\n#include "shader_recompiler/nx2_contract.h"\n')
replace(fp, """    if (flags.no_contraction) {
        ctx.Decorate(op, spv::Decoration::NoContraction);""", """    if (flags.no_contraction && !Nx2Contracts(ctx.stage)) {
        ctx.Decorate(op, spv::Decoration::NoContraction);""")

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
