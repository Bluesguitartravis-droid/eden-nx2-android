#!/usr/bin/env python3
"""Mario Kart World's terrain arrays (8192x8192, 68 layers, 12 levels; three of them) are far too
large to back whole, so upstream gives such arrays memory layer by layer as the game writes them
(SparseLayers). Two things keep that from working on Android:

1. Only ASTC that is decoded on the GPU qualifies. Adreno samples ASTC natively, so the two ASTC
   arrays fall through to a wholly unbacked image and read as zero: black roads and ground.
   Native ASTC uploads layer by layer like any plain format, so let it qualify.
2. A layered image gets at most 384 MiB (devices reporting under 7 GiB of GPU memory), four layers
   of the third array, and the texture cache budget of an integrated GPU is 4 GiB whatever the
   RAM. With 14 GiB or more of RAM: 1 GiB an image and a 6 GiB budget; with 20 GiB or more:
   2 GiB an image and an 8 GiB budget.

EDEN_NX2_TERRAIN_MEMORY=0 restores upstream's behaviour."""
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


def include(rel, lines):
    p = root / rel
    t = p.read_text()
    for line in lines:
        line = f"#include {line}\n"
        if line not in t:
            first = t.index("#include ")
            t = t[:first] + line + t[first:]
    p.write_text(t)


tc = "renderer_vulkan/vk_texture_cache.cpp"

patch(tc,
      """[[nodiscard]] bool UsesSparseLayers(const Device& device, const ImageInfo& info, GPUVAddr gpu_addr) {""",
      """/// NX2 Android: system RAM in GiB for the terrain memory limits, 0 with EDEN_NX2_TERRAIN_MEMORY=0.
[[nodiscard]] u64 Nx2TerrainRamGiB() {
    static const u64 gib = [] {
        const char* value = std::getenv("EDEN_NX2_TERRAIN_MEMORY");
        if (value != nullptr && value[0] == '0') {
            return u64{0};
        }
        return Common::GetMemInfo().TotalPhysicalMemory >> 30;
    }();
    return gib;
}

[[nodiscard]] bool UsesSparseLayers(const Device& device, const ImageInfo& info, GPUVAddr gpu_addr) {""",
      "Nx2TerrainRamGiB()")

patch(tc,
      """    const bool layer_uploads =
        IsHugeAstcArray(device, info) ||
        (!IsPixelFormatASTC(info.format) &&""",
      """    const bool layer_uploads =
        IsHugeAstcArray(device, info) ||
        (IsPixelFormatASTC(info.format) && device.IsOptimalAstcSupported() &&
         Nx2TerrainRamGiB() != 0) ||
        (!IsPixelFormatASTC(info.format) &&""",
      "device.IsOptimalAstcSupported() &&\n         Nx2TerrainRamGiB() != 0")

patch(tc,
      """[[nodiscard]] VkDeviceSize SparseLayersCap(const Device& device) {
""",
      """[[nodiscard]] VkDeviceSize SparseLayersCap(const Device& device) {
    if (const u64 ram = Nx2TerrainRamGiB(); ram >= 14) {
        return VkDeviceSize{ram >= 20 ? 2048U : 1024U} << 20;
    }
""",
      "const u64 ram = Nx2TerrainRamGiB(); ram >= 14")

# How many layers the game wrote against how many got memory (the first few times).
patch(tc,
      """    const VkDeviceSize cap = SparseLayersCap(device);
    std::vector<VkSparseImageMemoryBind> image_binds;""",
      """    const VkDeviceSize cap = SparseLayersCap(device);
    static std::atomic<u32> nx2_logged{};
    if (nx2_logged.fetch_add(1) < 24) {
        LOG_INFO(Render_Vulkan,
                 "NX2 sparse layered image {:#x}: {} of {} layers asked for, {} MiB bound so far, "
                 "{} KiB a layer, cap {} MiB",
                 gpu_addr, layers.size(), s.layer_memory.size(), s.bound_bytes >> 20,
                 s.layer_bytes >> 10, cap >> 20);
    }
    std::vector<VkSparseImageMemoryBind> image_binds;""",
      "NX2 sparse layered image")
include(tc, ["<atomic>", "<cstdlib>", '"common/memory_detect.h"'])

dev = "vulkan_common/vulkan_device.cpp"
patch(dev,
      """        const u64 memory_size = Settings::values.vram_usage_mode.GetValue() == Settings::VramUsageMode::Aggressive ? 6_GiB : 4_GiB;
""",
      """        u64 memory_size = Settings::values.vram_usage_mode.GetValue() == Settings::VramUsageMode::Aggressive ? 6_GiB : 4_GiB;
#ifdef __ANDROID__
        // NX2: an integrated GPU's memory is system RAM; with plenty of it, a larger budget
        // (Mario Kart World's terrain layers alone take 2-5 GiB).
        if (const char* value = std::getenv("EDEN_NX2_TERRAIN_MEMORY");
            value == nullptr || value[0] != '0') {
            const u64 ram = Common::GetMemInfo().TotalPhysicalMemory >> 30;
            memory_size = (std::max)(memory_size, ram >= 20 ? 8_GiB : ram >= 14 ? 6_GiB : 4_GiB);
            LOG_INFO(Render_Vulkan, "NX2 texture memory budget {} GiB ({} GiB of RAM)",
                     memory_size >> 30, ram);
        }
#endif
""",
      "NX2 texture memory budget")
include(dev, ['"common/memory_detect.h"', '"common/logging.h"'])
print("terrain layer arrays: native ASTC by layer, memory limits by system RAM")
