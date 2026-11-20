#!/usr/bin/env python3
"""Pipeline cache: the background saves take their own copy of the cache path.

On the Odin (builds 193, 198, 207) Eden crashed with SIGSEGV in a queued SerializeVulkanPipelineCache
on the serialization thread, in the copy of vulkan_pipeline_cache_filename at its start (memcpy from
null), right when "Shader precache: finished" queued the post-precache save; once the log showed
"Failed to open Vulkan driver pipeline cache file " with an empty path first. The member the worker
read was not the path the save was queued for.

The post-precache save now runs on the thread that ran the precache (the precache's builds have
ended; LoadDiskResources' own save does the same), and the timed flush captures a copy of the path
and skips an empty one.

Usage: apply-nx2-pipeline-cache-save-fix.py <upstream source root>
"""
import pathlib
import sys

path = pathlib.Path(sys.argv[1]) / "src/video_core/renderer_vulkan/vk_pipeline_cache.cpp"
text = path.read_text()
if "NX2 pipeline cache save" in text:
    print("vk_pipeline_cache.cpp: already patched")
    sys.exit(0)

edits = [
    ("""    serialization_thread.QueueWork([this] {
        SerializeVulkanPipelineCache(vulkan_pipeline_cache_filename, vulkan_pipeline_cache,
                                     CACHE_VERSION);
        size_t size = 0;
        vulkan_pipeline_cache.Read(&size, nullptr);
        last_cache_size.store(size, std::memory_order_relaxed);
        flush_in_flight.store(false, std::memory_order_release);
    });""",
     """    // NX2 pipeline cache save: the worker gets its own copy of the path.
    serialization_thread.QueueWork([this, filename = vulkan_pipeline_cache_filename] {
        if (!filename.empty()) {
            SerializeVulkanPipelineCache(filename, vulkan_pipeline_cache, CACHE_VERSION);
            size_t size = 0;
            vulkan_pipeline_cache.Read(&size, nullptr);
            last_cache_size.store(size, std::memory_order_relaxed);
        }
        flush_in_flight.store(false, std::memory_order_release);
    });"""),
    ("""    const auto save = [this] {
        SerializeVulkanPipelineCache(vulkan_pipeline_cache_filename, vulkan_pipeline_cache,
                                     CACHE_VERSION);""",
     """    const auto save = [this, filename = vulkan_pipeline_cache_filename] {
        SerializeVulkanPipelineCache(filename, vulkan_pipeline_cache, CACHE_VERSION);"""),
    ("""    if (!device.CanReadPipelineCacheDuringBuilds()) {
        save();
        return;
    }
    // Not skipped when a timed flush is in flight: that one may have read the cache before the
    // stage's last builds. The serialization thread runs the two one after the other.
    serialization_thread.QueueWork(save);""",
     """    // NX2 pipeline cache save: saved here, on the thread that ran the precache. Queued, it
    // crashed on the Odin reading a path that was not the one it was queued for.
    serialization_thread.WaitForRequests();
    save();"""),
]
for old, new in edits:
    if text.count(old) != 1:
        sys.exit(f"vk_pipeline_cache.cpp: expected one match for {old[:60]!r}, found {text.count(old)}")
    text = text.replace(old, new)
path.write_text(text)
print("vk_pipeline_cache.cpp: pipeline cache saves copy their path; post-precache save runs inline")
