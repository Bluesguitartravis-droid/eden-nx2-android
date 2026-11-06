#!/usr/bin/env python3
"""With host-mapped buffers, a CPU read of GPU-written buffer memory waits for the
buffer's LastUsageTick: the last submission that used the buffer at all, reads included.
DKB on NCE then waits 350-640 ms of every second in ~2,600 such reads. The bytes being
read only depend on the GPU's writes, so wait for the buffer's last GPU write instead:
shader/transform-feedback writes (MarkWrittenBuffer), and GPU copies or clears into it.
EDEN_NX2_DOWNLOAD_WAIT=write waits for the last GPU write, =usage for the last use; the
default reads the mapping without waiting."""
from pathlib import Path
import sys

root = Path(sys.argv[1]).resolve() / "src/video_core"

def patch(rel, old, new, marker):
    p = root / rel
    t = p.read_text()
    if marker in t:
        return
    if old not in t:
        raise SystemExit(f"{rel}: anchor not found: {old[:70]!r}")
    p.write_text(t.replace(old, new, 1))

# Buffer: a GPU write tick.
patch("renderer_vulkan/vk_buffer_cache.h",
      """    [[nodiscard]] u64 LastUsageTick() const noexcept {
        return last_usage_tick;
    }""",
      """    [[nodiscard]] u64 LastUsageTick() const noexcept {
        return last_usage_tick;
    }

    void Nx2MarkGpuWrite(u64 tick) noexcept {
        nx2_write_tick = (std::max)(nx2_write_tick, tick);
    }

    [[nodiscard]] u64 Nx2GpuWriteTick() const noexcept {
        return nx2_write_tick;
    }""",
      "Nx2MarkGpuWrite")
patch("renderer_vulkan/vk_buffer_cache.h",
      "    u64 last_usage_tick{};\n",
      "    u64 last_usage_tick{};\n    u64 nx2_write_tick{};\n",
      "u64 nx2_write_tick{};")

# Runtime: copies and clears into a cache Buffer record the write.
patch("renderer_vulkan/vk_buffer_cache.h",
      """    void ClearBuffer(VkBuffer dest_buffer, u32 offset, size_t size, u32 value);
""",
      """    void ClearBuffer(VkBuffer dest_buffer, u32 offset, size_t size, u32 value);

    void CopyBuffer(Buffer& dst_buffer, VkBuffer src_buffer,
                    std::span<const VideoCommon::BufferCopy> copies, bool barrier,
                    bool can_reorder_upload = false) {
        dst_buffer.Nx2MarkGpuWrite(CurrentTick());
        CopyBuffer(static_cast<VkBuffer>(dst_buffer), src_buffer, copies, barrier,
                   can_reorder_upload);
    }

    void ClearBuffer(Buffer& dest_buffer, u32 offset, size_t size, u32 value) {
        dest_buffer.Nx2MarkGpuWrite(CurrentTick());
        ClearBuffer(static_cast<VkBuffer>(dest_buffer), offset, size, value);
    }
""",
      "dst_buffer.Nx2MarkGpuWrite")

# Shader writes.
patch("buffer_cache/buffer_cache.h",
      """void BufferCache<P>::MarkWrittenBuffer(BufferId buffer_id, DAddr device_addr, u32 size, bool needs_sync) {
    if constexpr (!IS_OPENGL) {""",
      """void BufferCache<P>::MarkWrittenBuffer(BufferId buffer_id, DAddr device_addr, u32 size, bool needs_sync) {
    if constexpr (requires { slot_buffers[buffer_id].Nx2MarkGpuWrite(u64{}); }) {
        slot_buffers[buffer_id].Nx2MarkGpuWrite(runtime.CurrentTick());
    }
    if constexpr (!IS_OPENGL) {""",
      "slot_buffers[buffer_id].Nx2MarkGpuWrite(runtime.CurrentTick())")

# The mapped read waits for the last write.
patch("buffer_cache/buffer_cache.h",
      """            runtime.Wait(buffer.LastUsageTick());
            if (nx2_profile) {""",
      """            // 0: no wait (the GPU writes land in the mapping; the game syncs as on hardware),
            // 1: the buffer's last GPU write, 2: its last use.
            static const int nx2_wait_mode = [] {
                const char* v = std::getenv("EDEN_NX2_DOWNLOAD_WAIT");
                const std::string_view mode{v ? v : "write"};
                return mode == "usage" ? 2 : mode == "write" ? 1 : 0;
            }();
            u64 nx2_tick = buffer.LastUsageTick();
            if constexpr (requires { buffer.Nx2GpuWriteTick(); }) {
                if (nx2_wait_mode == 1) {
                    nx2_tick = buffer.Nx2GpuWriteTick();
                }
            }
            if (nx2_wait_mode != 0) {
                runtime.Wait(nx2_tick);
            }
            if (nx2_profile) {""",
      "EDEN_NX2_DOWNLOAD_WAIT")
for rel, incs in (("renderer_vulkan/vk_buffer_cache.h", ["<algorithm>"]),
                  ("buffer_cache/buffer_cache.h", ["<cstdlib>", "<string_view>"])):
    p = root / rel
    t = p.read_text()
    for inc in incs:
        line = f"#include {inc}\n"
        if line not in t:
            t = t.replace("#pragma once\n", "#pragma once\n\n" + line, 1)
    p.write_text(t)
print("download waits for the last GPU write")
