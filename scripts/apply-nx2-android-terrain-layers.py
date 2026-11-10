#!/usr/bin/env python3
"""Mario Kart World's terrain arrays (8192x8192, 68 layers, 12 levels; three of them) are far too
large to back whole, so upstream gives such arrays memory layer by layer as the game writes them
(SparseLayers). Two things keep that from working on Android:

1. Only ASTC that is decoded on the GPU qualifies. Adreno samples ASTC natively, so the two ASTC
   arrays fall through to a wholly unbacked image and read as zero: black roads and ground.
   Native ASTC uploads layer by layer like any plain format, so let it qualify.
2. The game writes into all 68 layers of each array, so whole layers at full size come to 4.9 GiB
   (build 148 bound them and Android killed the app while loading). Like upstream does for
   GPU-decoded ASTC arrays (TrimmedHostInfo), such an array keeps only its smaller mip levels on
   the host: at most 512 MiB an array (256 MiB with under 14 GiB of RAM), which is 4096x4096 for
   the ASTC arrays and 2048x2048 for the third. Layers upload in batches of at most 96 MiB.
3. A layered image got at most 384 MiB and an integrated GPU's texture budget is 4 GiB whatever
   the RAM: 1 GiB an image and a 6 GiB budget with 14 GiB or more of RAM.

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
        return VkDeviceSize{1024} << 20;
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
# Trimmed host shape for block-compressed SparseLayers arrays the host samples as they are.
patch(tc,
      """[[nodiscard]] bool IsCubeCompatible(const Device& device, const ImageInfo& info) {""",
      """/// NX2 Android: TrimmedHostInfo, and for a block-compressed layered array over 1 GiB that the
/// host samples as it is (SparseLayers), only the mip levels that fit the per-array limit.
[[nodiscard]] ImageInfo Nx2TrimmedHostInfo(const Device& device, const ImageInfo& info,
                                           u32& level_offset, GPUVAddr gpu_addr) {
    ImageInfo host = TrimmedHostInfo(device, info, level_offset);
    const u64 ram = Nx2TerrainRamGiB();
    if (level_offset != 0 || ram == 0 || IsHugeAstcArray(device, info) ||
        !(IsPixelFormatASTC(info.format) || IsPixelFormatBCn(info.format)) ||
        !UsesSparseLayers(device, info, gpu_addr)) {
        return host;
    }
    const u32 block_width = VideoCore::Surface::DefaultBlockWidth(info.format);
    const u32 block_height = VideoCore::Surface::DefaultBlockHeight(info.format);
    const u64 block_bytes = VideoCore::Surface::BytesPerBlock(info.format);
    const u32 levels = HostMipCount(info);
    const auto bytes_from = [&](u32 skip) {
        u64 bytes = 0;
        for (u32 level = skip; level < levels; ++level) {
            const u32 width = (std::max)(info.size.width >> level, 1U);
            const u32 height = (std::max)(info.size.height >> level, 1U);
            bytes += u64{Common::DivCeil(width, block_width)} *
                     Common::DivCeil(height, block_height) * block_bytes;
        }
        return bytes * static_cast<u64>(info.resources.layers);
    };
    const u64 whole = bytes_from(0);
    if (whole <= (u64{1} << 30)) {
        return host;
    }
    const u64 limit = (ram >= 14 ? u64{512} : u64{256}) << 20;
    while (level_offset + 1 < levels && bytes_from(level_offset) > limit) {
        ++level_offset;
    }
    host.size.width = (std::max)(info.size.width >> level_offset, 1U);
    host.size.height = (std::max)(info.size.height >> level_offset, 1U);
    host.resources.levels -= static_cast<s32>(level_offset);
    static std::atomic<u32> logged{};
    if (logged.fetch_add(1) < 24) {
        LOG_INFO(Render_Vulkan,
                 "NX2 layered array {:#x}: {}x{}x{} is {} MiB; keeping mip levels {}+ ({}x{}, {} MiB)",
                 gpu_addr, info.size.width, info.size.height, info.resources.layers, whole >> 20,
                 level_offset, host.size.width, host.size.height, bytes_from(level_offset) >> 20);
    }
    return host;
}

[[nodiscard]] bool IsCubeCompatible(const Device& device, const ImageInfo& info) {""",
      "Nx2TrimmedHostInfo(const Device& device")

p = root / tc
t = p.read_text()
for old, new in (
        ("TrimmedHostInfo(runtime_.device, info, host_level_offset)",
         "Nx2TrimmedHostInfo(runtime_.device, info, host_level_offset, gpu_addr_)"),
        ("const ImageInfo host = TrimmedHostInfo(device, info, offset);",
         "const ImageInfo host = Nx2TrimmedHostInfo(device, info, offset, gpu_addr);")):
    if new not in t:
        if old not in t:
            raise SystemExit(f"{tc}: anchor not found: {old}")
        t = t.replace(" " + old, " " + new).replace("(" + old, "(" + new)
        if new not in t:
            raise SystemExit(f"{tc}: call not replaced: {old}")
p.write_text(t)

# Plain layer uploads of a trimmed array: only the kept levels, a batch of layers at a time.
patch("texture_cache/texture_cache.h",
      """        if (False(image.flags & ImageFlagBits::AcceleratedUpload)) {
            // Plain formats: each layer unswizzled on its own into its layer of the image.
""",
      """        if (False(image.flags & ImageFlagBits::AcceleratedUpload) && image.host_level_offset != 0) {
            // NX2: a trimmed plain array. Each layer is unswizzled whole into a scratch buffer;
            // only the tail holding the kept levels goes to the staging buffer.
            ImageInfo layer_info = image.info;
            layer_info.resources.layers = 1;
            const size_t layer_size = CalculateUnswizzledSizeBytes(layer_info);
            const s32 skip = static_cast<s32>(image.host_level_offset);
            unswizzle_data_buffer.resize_destructive(layer_size);
            const std::span<u8> scratch{unswizzle_data_buffer.data(), layer_size};
            size_t tail_offset = 0;
            size_t done = 0;
            while (done < layers.size()) {
                boost::container::small_vector<BufferImageCopy, 16> first_copies;
                const auto unswizzle = [&](u32 layer) {
                    const GPUVAddr layer_addr = image.gpu_addr + u64{layer} * image.info.layer_stride;
                    Tegra::Memory::GpuGuestMemory<u8, Tegra::Memory::GuestMemoryFlags::UnsafeRead>
                        swizzle_data(*gpu_memory, layer_addr, image.info.layer_stride,
                                     &swizzle_data_buffer);
                    return UnswizzleImage(*gpu_memory, layer_addr, layer_info, swizzle_data, scratch);
                };
                if (done == 0) {
                    first_copies = unswizzle(layers[0]);
                    tail_offset = layer_size;
                    size_t dropped_end = 0;
                    for (const BufferImageCopy& copy : first_copies) {
                        if (copy.image_subresource.base_level >= skip) {
                            tail_offset = (std::min)(tail_offset, copy.buffer_offset);
                        } else {
                            dropped_end = (std::max)(dropped_end, copy.buffer_offset + 1);
                        }
                    }
                    if (tail_offset >= layer_size || dropped_end > tail_offset) {
                        LOG_ERROR(HW_GPU, "NX2 layered array {:#x}: unexpected level layout, not uploaded",
                                  image.gpu_addr);
                        return;
                    }
                }
                const size_t tail = layer_size - tail_offset;
                const size_t batch = (std::min)(layers.size() - done,
                                                (std::max)(size_t{1}, (size_t{96} << 20) / tail));
                auto staging = runtime.UploadStagingBuffer(tail * batch);
                std::vector<BufferImageCopy> copies;
                for (size_t i = 0; i < batch; ++i) {
                    const u32 layer = layers[done + i];
                    const auto layer_copies =
                        done == 0 && i == 0 ? std::move(first_copies) : unswizzle(layer);
                    std::memcpy(staging.mapped_span.data() + i * tail, scratch.data() + tail_offset,
                                tail);
                    for (BufferImageCopy copy : layer_copies) {
                        if (copy.image_subresource.base_level < skip) {
                            continue;
                        }
                        copy.buffer_offset = copy.buffer_offset - tail_offset + i * tail;
                        copy.image_subresource.base_level -= skip;
                        copy.image_subresource.base_layer = static_cast<s32>(layer);
                        copies.push_back(copy);
                    }
                }
                image.UploadMemory(staging, copies);
                done += batch;
            }
            ++images_uploaded;
            uploaded_bytes += u64{image.info.layer_stride} * layers.size();
            ++layer_uploads;
            return;
        }
        if (False(image.flags & ImageFlagBits::AcceleratedUpload)) {
            // Plain formats: each layer unswizzled on its own into its layer of the image.
""",
      "NX2: a trimmed plain array")
include(tc, ["<atomic>", "<cstdlib>", '"common/memory_detect.h"'])
include("texture_cache/texture_cache.h", ["<cstring>"])

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
            memory_size = (std::max)(memory_size, ram >= 14 ? 6_GiB : 4_GiB);
            LOG_INFO(Render_Vulkan, "NX2 texture memory budget {} GiB ({} GiB of RAM)",
                     memory_size >> 30, ram);
        }
#endif
""",
      "NX2 texture memory budget")
include(dev, ['"common/memory_detect.h"', '"common/logging.h"'])
print("terrain layer arrays: native ASTC by layer, memory limits by system RAM")
