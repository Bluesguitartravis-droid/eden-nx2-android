#!/usr/bin/env python3
"""EDEN_NX2_SKIP_MESH (measurement aid, live; needs apply-nx2-skip-shaders.py first):
  1  drop every emulated mesh draw (its compute dispatches and its draw)
  2  drop them too, but still end the render pass and record the barriers each one costs (the
     next draw begins the pass again), to tell the price of the render pass breaks from the mesh programs' own work.
Ocarina sends ~90 mesh draws a frame; on a device without VK_EXT_mesh_shader each one ends the
render pass, runs 1-3 compute dispatches behind full barriers and begins the pass again."""
from pathlib import Path
import sys

path = Path(sys.argv[1]).resolve() / "src/video_core/renderer_vulkan/vk_rasterizer.cpp"
text = path.read_text()

anchor = """        for (const u64 hash : pipeline->ShaderHashes()) {
            if (hash != 0 && VideoCommon::Nx2SkipShaderHash(hash)) {
                nx2_skipped_draws.fetch_add(1, std::memory_order_relaxed);
                return;
            }
        }
"""
if anchor not in text:
    sys.exit("skip-mesh: skip-shaders hook not found")

addition = """        // NX2 skip mesh (scripts/apply-nx2-skip-mesh.py)
        if (pipeline->IsMeshPipeline() && !device.IsExtMeshShaderSupported()) {
            const std::string mode = VideoCommon::Nx2LiveSwitch("EDEN_NX2_SKIP_MESH");
            if (mode == "1" || mode == "2") {
                if (mode == "2") {
                    scheduler.RequestOutsideRenderPassOperationContext();
                    scheduler.Record([](vk::CommandBuffer cmdbuf) {
                        const VkMemoryBarrier barrier{
                            .sType = VK_STRUCTURE_TYPE_MEMORY_BARRIER,
                            .pNext = nullptr,
                            .srcAccessMask = VK_ACCESS_MEMORY_WRITE_BIT,
                            .dstAccessMask = VK_ACCESS_MEMORY_READ_BIT | VK_ACCESS_MEMORY_WRITE_BIT,
                        };
                        cmdbuf.PipelineBarrier(VK_PIPELINE_STAGE_ALL_GRAPHICS_BIT |
                                                   VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT,
                                               VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT, 0, barrier);
                        cmdbuf.PipelineBarrier(VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT,
                                               VK_PIPELINE_STAGE_VERTEX_SHADER_BIT, 0, barrier);
                    });
                }
                nx2_skipped_draws.fetch_add(1, std::memory_order_relaxed);
                return;
            }
        }
"""
text = text.replace(anchor, anchor + addition, 1)
path.write_text(text)
print("skip-mesh: EDEN_NX2_SKIP_MESH hook added")
