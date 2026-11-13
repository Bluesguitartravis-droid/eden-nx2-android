#!/usr/bin/env python3
"""Log the shape of every shader that reads memory through pointers (global memory).

Ocarina of Time is GPU-bound on Turnip (81-87 % busy at 7-20 fps). Banana Native measured its
shaders that load through pointers at 146-171 ms on Turnip against 2.5 ms on Qualcomm's driver:
each load runs a search over the candidate buffers. Ours does too (DefineGlobalMemoryFunctions:
every call computes every candidate's base, two memory reads for each indirect one, then branches).
To see whether that is where Ocarina's GPU time goes, each translated graphics shader that uses
global memory gets one line (once per hash):

  NX2_GMEM s1:<hash> loads=<n> stores=<n> atomics=<n> candidates=<n> indirect=<n> native=<0|1>

and EDEN_DKB_FRAME_TRACE=1 (an existing switch) names the slowest draws by the same hashes.
First built in build 164.
Usage: python3 scripts/apply-nx2-global-memory-log.py <upstream-root>
"""
from pathlib import Path
import sys

path = Path(sys.argv[1]) / "src/video_core/renderer_vulkan/vk_pipeline_cache.cpp"
text = path.read_text()
MARK = "NX2_GMEM"
if MARK in text:
    print("already patched")
    raise SystemExit(0)

anchor = ("        if (Settings::values.dump_guest_shaders) {\n"
          "            env.Dump(hash, key.unique_hashes[index]);\n"
          "        }\n")
if text.count(anchor) != 1:
    raise SystemExit("vk_pipeline_cache.cpp: graphics dump_guest_shaders block not found")
text = text.replace(anchor, anchor + r'''
        if (programs[index].info.uses_global_memory) {
            // NX2_GMEM: the shape of a shader that loads through pointers, once per hash.
            static std::mutex nx2_gmem_mutex;
            static std::unordered_set<u64> nx2_gmem_logged;
            const u64 nx2_hash{key.unique_hashes[index]};
            bool nx2_first{};
            {
                std::scoped_lock nx2_lock{nx2_gmem_mutex};
                nx2_first = nx2_gmem_logged.insert(nx2_hash).second;
            }
            if (nx2_first) {
                u32 loads{}, stores{}, atomics{};
                for (const auto* block : programs[index].post_order_blocks) {
                    for (const auto& inst : block->Instructions()) {
                        switch (inst.GetOpcode()) {
                        case Shader::IR::Opcode::LoadGlobalU8:
                        case Shader::IR::Opcode::LoadGlobalS8:
                        case Shader::IR::Opcode::LoadGlobalU16:
                        case Shader::IR::Opcode::LoadGlobalS16:
                        case Shader::IR::Opcode::LoadGlobal32:
                        case Shader::IR::Opcode::LoadGlobal64:
                        case Shader::IR::Opcode::LoadGlobal128:
                            ++loads;
                            break;
                        case Shader::IR::Opcode::WriteGlobalU8:
                        case Shader::IR::Opcode::WriteGlobalS8:
                        case Shader::IR::Opcode::WriteGlobalU16:
                        case Shader::IR::Opcode::WriteGlobalS16:
                        case Shader::IR::Opcode::WriteGlobal32:
                        case Shader::IR::Opcode::WriteGlobal64:
                        case Shader::IR::Opcode::WriteGlobal128:
                            ++stores;
                            break;
                        default:
                            if (inst.GetOpcode() >= Shader::IR::Opcode::GlobalAtomicIAdd32 &&
                                inst.GetOpcode() <= Shader::IR::Opcode::GlobalAtomicExchange64) {
                                ++atomics;
                            }
                            break;
                        }
                    }
                }
                const auto& descriptors{programs[index].info.storage_buffers_descriptors};
                const auto indirect{std::ranges::count_if(descriptors, [](const auto& desc) {
                    return desc.indirect_offset != ~0U;
                })};
                LOG_INFO(Render_Vulkan,
                         "NX2_GMEM s{}:{:016x} loads={} stores={} atomics={} candidates={} "
                         "indirect={} native={}",
                         index, nx2_hash, loads, stores, atomics, descriptors.size(), indirect,
                         programs[index].info.native_global_bounds ? 1 : 0);
            }
        }
''', 1)
for inc in ("#include <mutex>\n", "#include <unordered_set>\n"):
    if inc not in text:
        text = text.replace("#include <algorithm>\n", "#include <algorithm>\n" + inc, 1)
path.write_text(text)
print("NX2_GMEM shader logging installed")
