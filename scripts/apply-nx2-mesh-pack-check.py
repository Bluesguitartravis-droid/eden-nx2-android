#!/usr/bin/env python3
"""Mesh programs: can two mesh workgroups share one Vulkan workgroup? (diagnostic, build 285)

Mesh emulation runs each 32-invocation mesh workgroup as its own Vulkan compute workgroup; Adreno
waves are at least 64 wide, so half of each wave idles. Packing two mesh workgroups into one is only
safe when the program's barriers are reached by every invocation of both halves (all barriers in
top-level code, no early return inside a branch), it uses no warp operations (a guest warp would
share a subgroup with the other half), and it writes no global/storage memory or images (the
odd last half runs with a workgroup index past the draw's). This logs, for each emulated mesh
pipeline built, "Mesh packing check: ... packable yes/no (reasons)" with its stage hashes, so the
costly ones (EDEN_NX2_MESH_STATS) can be matched. No behaviour change.
"""
from pathlib import Path
import sys

path = Path(sys.argv[1]).resolve() / "src/video_core/renderer_vulkan/vk_pipeline_cache.cpp"
text = path.read_text()
old = '''                     mesh_emulation->payload_stride, reads.empty() ? " none" : reads);
        }
'''
new = old + '''        // Mesh packing check (scripts/apply-nx2-mesh-pack-check.py): log only.
        static std::atomic<u32> pack_checked{};
        if (pack_checked.fetch_add(1) < 128) {
            u32 depth{};
            u32 barriers{};
            u32 nested_barriers{};
            u32 epilogues{};
            u32 nested_epilogues{};
            u32 nested_returns{};
            u32 loops{};
            u32 instructions{};
            u32 memory_writes{};
            std::string writes_seen;
            for (const Shader::IR::AbstractSyntaxNode& node : mesh.syntax_list) {
                using NodeType = Shader::IR::AbstractSyntaxNode::Type;
                switch (node.type) {
                case NodeType::If:
                case NodeType::Loop:
                    ++depth;
                    loops += node.type == NodeType::Loop ? 1 : 0;
                    break;
                case NodeType::EndIf:
                case NodeType::Repeat:
                    depth = depth > 0 ? depth - 1 : 0;
                    break;
                case NodeType::Return:
                    nested_returns += depth > 0 ? 1 : 0;
                    break;
                case NodeType::Block:
                    for (const Shader::IR::Inst& inst : node.data.block->Instructions()) {
                        ++instructions;
                        const Shader::IR::Opcode op{inst.GetOpcode()};
                        const std::string_view name{Shader::IR::NameOf(op)};
                        if (op == Shader::IR::Opcode::Barrier ||
                            op == Shader::IR::Opcode::BarrierVoteInit ||
                            op == Shader::IR::Opcode::BarrierVote) {
                            ++barriers;
                            nested_barriers += depth > 0 ? 1 : 0;
                        } else if (op == Shader::IR::Opcode::Epilogue) {
                            ++epilogues;
                            nested_epilogues += depth > 0 ? 1 : 0;
                        } else if (name.starts_with("WriteGlobal") ||
                                   name.starts_with("GlobalAtomic") ||
                                   name.starts_with("WriteStorage") ||
                                   name.starts_with("StorageAtomic") ||
                                   (name.starts_with("Image") &&
                                    (name.find("Write") != std::string_view::npos ||
                                     name.find("Atomic") != std::string_view::npos))) {
                            if (memory_writes++ < 4) {
                                writes_seen += fmt::format(" {}", name);
                            }
                        }
                    }
                    break;
                default:
                    break;
                }
            }
            const Shader::Info& minfo{mesh.info};
            const bool warp_ops{minfo.uses_subgroup_vote || minfo.uses_subgroup_invocation_id ||
                                minfo.uses_subgroup_shuffles || minfo.uses_subgroup_mask ||
                                minfo.uses_quad_shuffles || minfo.uses_fswzadd};
            std::string reasons;
            const auto reason = [&](bool bad, std::string_view what) {
                if (bad) {
                    reasons += fmt::format("{}{}", reasons.empty() ? "" : ", ", what);
                }
            };
            reason(has_task, "task program");
            reason(mesh.workgroup_size[0] != 32 || mesh.workgroup_size[1] != 1 ||
                       mesh.workgroup_size[2] != 1,
                   "workgroup not 32x1x1");
            reason(warp_ops, "warp operations");
            reason(nested_barriers != 0, "barrier in a branch or loop");
            reason(nested_epilogues != 0, "epilogue in a branch");
            reason(nested_returns != 0, "return in a branch");
            reason(memory_writes != 0, "writes memory");
            std::string stages;
            for (size_t stage = 0; stage < key.unique_hashes.size(); ++stage) {
                if (key.unique_hashes[stage] != 0) {
                    stages += fmt::format("{}s{}:{:016x}", stages.empty() ? "" : "+", stage,
                                          key.unique_hashes[stage]);
                }
            }
            LOG_INFO(Render_Vulkan,
                     "Mesh packing check: {} | packable {}{}{} | workgroup {}x{}x{}, shared {} "
                     "bytes, {} instructions, {} loops, barriers {} ({} nested), epilogues {} "
                     "({} nested), returns in branches {}, memory writes {}{}, warp ops {} "
                     "(vote {} lane {} shuffle {} mask {} quad {})",
                     stages, reasons.empty() ? "yes" : "no", reasons.empty() ? "" : ": ", reasons,
                     mesh.workgroup_size[0], mesh.workgroup_size[1], mesh.workgroup_size[2],
                     mesh.shared_memory_size, instructions, loops, barriers, nested_barriers,
                     epilogues, nested_epilogues, nested_returns, memory_writes, writes_seen,
                     warp_ops, minfo.uses_subgroup_vote, minfo.uses_subgroup_invocation_id,
                     minfo.uses_subgroup_shuffles, minfo.uses_subgroup_mask,
                     minfo.uses_quad_shuffles);
        }
'''
if text.count(old) != 1:
    sys.exit(f"apply-nx2-mesh-pack-check: anchor found {text.count(old)} times")
text = text.replace(old, new)
path.write_text(text)
print("apply-nx2-mesh-pack-check: applied")
