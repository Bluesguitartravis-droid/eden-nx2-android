#!/usr/bin/env python3
"""EDEN_NX2_MESH_HOIST=1 (live, read at each render pass begin): run the compute dispatches of an
emulated mesh draw before the render pass it is drawn in, instead of ending that pass for them.

On a device without VK_EXT_mesh_shader every mesh draw runs its task/mesh programs as compute
dispatches, which must be outside a render pass: Ocarina's ~90 mesh draws a frame each ended the
pass (attachment barriers, store and load), drained the GPU behind 2-4 barriers and began the pass
again. Hoisted, they run as a group in front of the pass's begin, and the pass stays whole.

Safe because nothing between a pass's begin and a draw in it can produce what the mesh programs
read: uploads, copies and the game's own compute passes all end the pass first. Not hoisted when
a draw in the pass wrote storage (render_pass_has_stores) or once the pass's begin has already
reached the GPU worker (the pass's prologue is sealed then): the dispatch ends the pass as before.
To let the prologue stay open, the scheduler holds full command chunks (up to 64) while a
hoisting pass is active and hands them to the worker when the pass ends.

Each dispatch used the pipeline's one mesh_descriptor_set for its draw; hoisted, several draws of
one pipeline dispatch before any of them draws, so each draw now has its own set slot.
NX2_MESH_HOIST every 2,000 mesh draws counts hoisted and pass-breaking ones."""
from pathlib import Path
import sys

root = Path(sys.argv[1]).resolve() / "src/video_core/renderer_vulkan"


def replace(path, old, new, count=1):
    text = path.read_text()
    if text.count(old) != count:
        sys.exit(f"mesh-hoist: {path.name}: expected {count} match(es), found {text.count(old)}:\n{old}")
    path.write_text(text.replace(old, new))


# --- Scheduler -------------------------------------------------------------------------------
h = root / "vk_scheduler.h"
replace(h, """    /// Sends currently recorded work to the worker thread.
    void DispatchWork();
""", """    /// Sends currently recorded work to the worker thread. With `may_hold` (a full chunk), keeps
    /// it back while a pass that hoists mesh dispatches is active (Nx2HoistBeforeRenderPass).
    void DispatchWork(bool may_hold = false);

    /// Runs `command` right before the active render pass's begin, if that pass still can take
    /// it (EDEN_NX2_MESH_HOIST). False: the caller ends the pass and records it in order.
    bool Nx2HoistBeforeRenderPass(std::function<void(vk::CommandBuffer)> command);
""")
replace(h, """        if (chunk->Record(command)) {
            return;
        }
        DispatchWork();
        (void)chunk->Record(command);
    }
""", """        if (chunk->Record(command)) {
            return;
        }
        DispatchWork(true);
        (void)chunk->Record(command);
    }
""")
replace(h, """    std::unique_ptr<CommandChunk> chunk;
""", """    std::unique_ptr<CommandChunk> chunk;
    /// NX2 mesh hoist: commands run before the active pass's begin, sealed once it ran.
    struct Nx2PassPrologue {
        std::mutex mutex;
        std::vector<std::function<void(vk::CommandBuffer)>> commands;
        bool sealed{};
    };
    std::shared_ptr<Nx2PassPrologue> nx2_prologue;
    std::vector<std::unique_ptr<CommandChunk>> nx2_held_chunks;
""")

cpp = root / "vk_scheduler.cpp"
replace(cpp, '#include "video_core/renderer_vulkan/vk_scheduler.h"\n',
        '#include "video_core/renderer_vulkan/vk_scheduler.h"\n#include "video_core/nx2_live_switch.h"\n')
replace(cpp, """void Scheduler::DispatchWork() {""", """bool Scheduler::Nx2HoistBeforeRenderPass(std::function<void(vk::CommandBuffer)> command) {
    if (!state.renderpass || !nx2_prologue || render_pass_has_stores) {
        return false;
    }
    std::scoped_lock lock{nx2_prologue->mutex};
    if (nx2_prologue->sealed) {
        return false;
    }
    nx2_prologue->commands.push_back(std::move(command));
    return true;
}

void Scheduler::DispatchWork(bool may_hold) {""")
replace(cpp, """    if (uploads_pending) {
        // The worker must receive everything recorded so far.
        FlushUploads();
    }
    if (chunk && !chunk->Empty()) {""", """    if (uploads_pending) {
        // The worker must receive everything recorded so far.
        FlushUploads();
    }
    // NX2 mesh hoist: keep the pass's chunks until it ends, so its begin (and the prologue run
    // there) reaches the worker after every dispatch hoisted into it.
    if (may_hold && nx2_prologue && state.renderpass && chunk && !chunk->Empty() &&
        nx2_held_chunks.size() < 64) {
        nx2_held_chunks.push_back(std::move(chunk));
        AcquireNewChunk();
        return;
    }
    if (!nx2_held_chunks.empty()) {
        bool wake;
        {
            std::scoped_lock ql{queue_mutex};
            for (auto& held : nx2_held_chunks) {
                work_queue.push(std::move(held));
            }
            wake = worker_waiting;
        }
        nx2_held_chunks.clear();
        if (wake) {
            event_cv.notify_all();
        }
    }
    if (chunk && !chunk->Empty()) {""")
replace(cpp, """    const bool after_compute = std::exchange(compute_writes_pending, false);
    Record([renderpass, framebuffer_handle, render_area, values, clear_value_count,
            after_compute](vk::CommandBuffer cmdbuf) {
""", """    const bool after_compute = std::exchange(compute_writes_pending, false);
    nx2_prologue.reset();
    if (VideoCommon::Nx2LiveSwitch("EDEN_NX2_MESH_HOIST") == "1") {
        nx2_prologue = std::make_shared<Nx2PassPrologue>();
    }
    Record([renderpass, framebuffer_handle, render_area, values, clear_value_count,
            after_compute, prologue = nx2_prologue](vk::CommandBuffer cmdbuf) {
        if (prologue) {
            // Hoisted mesh dispatches: each orders itself after earlier writes and before the
            // vertex reads of its draw.
            std::scoped_lock lock{prologue->mutex};
            prologue->sealed = true;
            for (auto& command : prologue->commands) {
                command(cmdbuf);
            }
            prologue->commands.clear();
        }
""")
replace(cpp, """        state.renderpass = VkRenderPass{};
        num_renderpass_images = 0;
    }
""", """        state.renderpass = VkRenderPass{};
        num_renderpass_images = 0;
        nx2_prologue.reset();
        if (!nx2_held_chunks.empty()) {
            DispatchWork();
        }
    }
""")

# --- Graphics pipeline -----------------------------------------------------------------------
gh = root / "vk_graphics_pipeline.h"
replace(gh, """    VkDescriptorSet mesh_descriptor_set{}; ///< shared by the dispatch and the draw it feeds
""", """    VkDescriptorSet mesh_descriptor_set{}; ///< shared by the dispatch and the draw it feeds
    /// NX2 mesh hoist: the set of one draw, shared by its dispatch and its draw only
    std::shared_ptr<VkDescriptorSet> nx2_mesh_set_slot;
""")

gp = root / "vk_graphics_pipeline.cpp"
replace(gp, """        scheduler.RequestOutsideRenderPassOperationContext();
        if (!IsBuilt()) {
            scheduler.Record([this](vk::CommandBuffer) {
                const VideoCommon::Nx2Profile::Scope scope{
                    VideoCommon::Nx2Profile::Zone::PipelineWait};
                build->Wait(!worker_only_build);
            });
        }
        scheduler.Record([this, descriptor_data, groups = mesh_groups, capacity = mesh_capacity,
""", """        nx2_mesh_set_slot = std::make_shared<VkDescriptorSet>(VkDescriptorSet{});
        auto nx2_mesh_command = [this, slot = nx2_mesh_set_slot, wait_build = !IsBuilt(),
                          descriptor_data, groups = mesh_groups, capacity = mesh_capacity,
""")
replace(gp, """            mesh_descriptor_set = VK_NULL_HANDLE;
            const bool has_task{mesh_emulation->has_task};
""", """            if (wait_build) {
                const VideoCommon::Nx2Profile::Scope scope{
                    VideoCommon::Nx2Profile::Zone::PipelineWait};
                build->Wait(!worker_only_build);
            }
            *slot = VkDescriptorSet{};
            const bool has_task{mesh_emulation->has_task};
""")
replace(gp, """            mesh_descriptor_set = descriptor_set;
""", """            *slot = descriptor_set;
""")
replace(gp, """        });
    }
    const u64 render_pass = deferred != nullptr ? scheduler.RenderPassSerial() : 0;
""", """        };
        // NX2 mesh hoist (scripts/apply-nx2-mesh-hoist.py)
        const bool hoisted = scheduler.IsRenderPassActive() &&
                             scheduler.Nx2HoistBeforeRenderPass(nx2_mesh_command);
        if (!hoisted) {
            scheduler.RequestOutsideRenderPassOperationContext();
            scheduler.Record(std::move(nx2_mesh_command));
        }
        static u64 nx2_hoisted{}, nx2_in_order{};
        ++(hoisted ? nx2_hoisted : nx2_in_order);
        if ((nx2_hoisted + nx2_in_order) % 2000 == 0) {
            LOG_INFO(Render_Vulkan, "NX2_MESH_HOIST mesh dispatches: {} hoisted, {} in order",
                     nx2_hoisted, nx2_in_order);
        }
    }
    const u64 render_pass = deferred != nullptr ? scheduler.RenderPassSerial() : 0;
""")
replace(gp, """    scheduler.Record([this, descriptor_data, bind_pipeline, update_descriptors, binding,
                      bind_descriptor_buffer, update_rescaling](vk::CommandBuffer cmdbuf) {
""", """    scheduler.Record([this, descriptor_data, bind_pipeline, update_descriptors, binding,
                      bind_descriptor_buffer, update_rescaling,
                      mesh_slot = mesh_emulation ? nx2_mesh_set_slot
                                                 : std::shared_ptr<VkDescriptorSet>{}](
                         vk::CommandBuffer cmdbuf) {
""")
replace(gp, """            if (mesh_descriptor_set) {
                cmdbuf.BindDescriptorSets(VK_PIPELINE_BIND_POINT_GRAPHICS, pipeline_layout, 0,
                                          mesh_descriptor_set, nullptr);
""", """            if (mesh_slot && *mesh_slot) {
                cmdbuf.BindDescriptorSets(VK_PIPELINE_BIND_POINT_GRAPHICS, pipeline_layout, 0,
                                          *mesh_slot, nullptr);
""")
print("mesh-hoist: EDEN_NX2_MESH_HOIST added")
