#!/usr/bin/env python3
"""With host-mapped buffers (apply-nx2-mapped-buffers.py), a read of GPU-written buffer memory
copies from the mapping once the GPU ran the writes. Upstream ends such a read with a host-read
barrier and a full Scheduler::Finish (written for MoltenVK): the whole GPU, however long ago the
buffer was written. The bytes only depend on the buffer's own GPU writes, and every submission
here ends with a host-read barrier, so wait for the submission holding the buffer's last GPU
write instead: shader and transform-feedback writes (MarkWrittenBuffer) and GPU copies or clears
into it. Donkey Kong Bananza waited 350-640 ms of every second in these reads before this.

EDEN_NX2_DOWNLOAD_WAIT=write (default) waits for the last GPU write, =usage for the buffer's last
use, =finish as upstream, =none not at all (breaks DKB's fur and geometry; for tests only)."""
from pathlib import Path
import sys

root = Path(sys.argv[1]).resolve() / "src/video_core"


def patch(rel, old, new, marker, count=1):
    p = root / rel
    t = p.read_text()
    if marker in t:
        return
    if t.count(old) < count:
        raise SystemExit(f"{rel}: anchor not found: {old[:70]!r}")
    p.write_text(t.replace(old, new, count))


# Buffer: the tick of its last GPU write.
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

# Runtime: copies and clears into a cache Buffer record the write; the mapped-read wait.
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

    /// NX2: before a host read of mapped buffers. Waits for the submission of their last GPU
    /// write (EDEN_NX2_DOWNLOAD_WAIT: write, usage, finish, none), not for the whole GPU.
    void Nx2WaitMappedRead(u64 write_tick, u64 usage_tick);
""",
      "dst_buffer.Nx2MarkGpuWrite")

patch("renderer_vulkan/vk_buffer_cache.cpp",
      """void BufferCacheRuntime::FinishForHostRead() {""",
      """void BufferCacheRuntime::Nx2WaitMappedRead(u64 write_tick, u64 usage_tick) {
    // 0: none, 1: the last GPU write, 2: the last use, 3: upstream's full finish.
    static const int mode = [] {
        const char* value = std::getenv("EDEN_NX2_DOWNLOAD_WAIT");
        const std::string_view name{value != nullptr ? value : "write"};
        return name == "finish" ? 3 : name == "usage" ? 2 : name == "none" ? 0 : 1;
    }();
    const bool profile = VideoCommon::Nx2Profile::Enabled();
    const u64 start = profile ? VideoCommon::Nx2Profile::Now() : 0;
    if (mode == 3) {
        FinishForHostRead();
    } else if (mode != 0) {
        // On any thread: only the scheduler's owner submits (WaitForHostRead asks it to).
        WaitForHostRead(mode == 2 ? usage_tick : write_tick);
    }
    if (profile) {
        VideoCommon::Nx2Profile::g_nx2_mapped_downloads.fetch_add(1, std::memory_order_relaxed);
        VideoCommon::Nx2Profile::g_nx2_mapped_download_ns.fetch_add(
            VideoCommon::Nx2Profile::Now() - start, std::memory_order_relaxed);
    }
}

void BufferCacheRuntime::FinishForHostRead() {""",
      "BufferCacheRuntime::Nx2WaitMappedRead")

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

# One buffer's mapped read (DownloadBufferMemory).
patch("buffer_cache/buffer_cache.h",
      """        if (const auto mapped = buffer.Mapped(); !mapped.empty()) {
            runtime.FinishForHostRead();
""",
      """        if (const auto mapped = buffer.Mapped(); !mapped.empty()) {
            if constexpr (requires { runtime.Nx2WaitMappedRead(u64{}, u64{}); }) {
                runtime.Nx2WaitMappedRead(buffer.Nx2GpuWriteTick(), buffer.LastUsageTick());
            } else {
                runtime.FinishForHostRead();
            }
""",
      "runtime.Nx2WaitMappedRead(buffer.Nx2GpuWriteTick()")

# Several buffers' mapped reads at once (DownloadRangesMemory), none staged.
patch("buffer_cache/buffer_cache.h",
      """        if (staged.empty()) {
            if constexpr (has_mapped) {
                runtime.FinishForHostRead();
            }
""",
      """        if (staged.empty()) {
            if constexpr (has_mapped) {
                if constexpr (requires { runtime.Nx2WaitMappedRead(u64{}, u64{}); }) {
                    u64 nx2_write_tick = 0;
                    u64 nx2_usage_tick = 0;
                    for (const Download& download : mapped) {
                        const Buffer& nx2_buffer = slot_buffers[download.buffer_id];
                        nx2_write_tick = (std::max)(nx2_write_tick, nx2_buffer.Nx2GpuWriteTick());
                        nx2_usage_tick = (std::max)(nx2_usage_tick, nx2_buffer.LastUsageTick());
                    }
                    runtime.Nx2WaitMappedRead(nx2_write_tick, nx2_usage_tick);
                } else {
                    runtime.FinishForHostRead();
                }
            }
""",
      "runtime.Nx2WaitMappedRead(nx2_write_tick, nx2_usage_tick)")

for rel, incs in (("renderer_vulkan/vk_buffer_cache.h", ["<algorithm>"]),
                  ("renderer_vulkan/vk_buffer_cache.cpp", ["<cstdlib>", "<string_view>"])):
    p = root / rel
    t = p.read_text()
    for inc in incs:
        line = f"#include {inc}\n"
        if line not in t:
            if "#pragma once\n" in t:
                t = t.replace("#pragma once\n", "#pragma once\n\n" + line, 1)
            else:
                t = line + t
    p.write_text(t)
print("mapped buffer reads wait for the last GPU write")
