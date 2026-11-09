#!/usr/bin/env python3
"""Never look up a staging buffer of size 0.

Ocarina of Time, right after its opening video (build 139): a texture whose descriptor reads
format 0 has no bytes, and its upload asked the staging pool for 0 bytes. Log2Ceil(0) indexes
past the 64 size classes and the GPU thread crashed in TryGetReservedBuffer. Round 0 up to 1.
"""
from pathlib import Path
import sys

f = Path(sys.argv[1]) / "src/video_core/renderer_vulkan/vk_staging_buffer_pool.cpp"
t = f.read_text()
old = """StagingBufferRef StagingBufferPool::GetStagingBuffer(size_t size, MemoryUsage usage,
                                                     bool deferred) {
"""
new = old + """    size = (std::max<size_t>)(size, 1); // NX2: Log2Ceil(0) is past the size classes
"""
if "NX2: Log2Ceil(0)" in t:
    print("staging zero-size guard already applied"); sys.exit(0)
if old not in t:
    raise SystemExit("vk_staging_buffer_pool.cpp: GetStagingBuffer shape changed")
f.write_text(t.replace(old, new, 1))
print("staging buffers of size 0 rounded up to 1")
