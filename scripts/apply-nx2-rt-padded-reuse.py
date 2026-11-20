#!/usr/bin/env python3
"""Render targets draw into the texture image they share memory with when only the width padding differs.

Build 200 on the Odin, Ocarina in Hyrule Field at ~13 fps: NX2_COPIES counted ~4,900 alias copies a
second (~370 a frame), almost all between two images at the same address that differ only in width
padding: D32_FLOAT 432x240 -> 427x240 (~3,000/s), 864x480 -> 854x480 (~900/s), B10G11R11 864x480 ->
854x480, ... The render target registers give the padded width (432 = 427 rounded up to the 64-byte
GOB row for 4-byte texels) and the texture descriptor the real one (427). Both have the same
block-linear layout, but the render target lookup matches sizes strictly, so the texture cache keeps
two images and copies the render target into the texture image every time it is drawn to, each copy
ending a render pass.

Now a render target first looks for a texture image (ImageFlagBits::Strong) at the same address with
the same format, height, samples, layers and block sizes, a smaller width and the same block-linear
aligned size, and draws into that one. The texture keeps its own size, so sampling is unchanged;
drawing is clipped to the texture's width, and the columns past it are padding no texture of that size
can read. Off by default: build 204 on the Odin washed Ocarina's fog and light out white and did not
change FPS. EDEN_NX2_RT_PADDED_REUSE=1 turns it on for experiments.

Usage: apply-nx2-rt-padded-reuse.py <upstream source root>
"""
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
path = root / "src/video_core/texture_cache/texture_cache.h"
text = path.read_text()
MARK = "NX2_RT_PADDED"
if MARK in text:
    print("texture_cache.h: already patched")
    sys.exit(0)

old = """    ImageId image_id{};
    bool delete_state = has_deleted_images;
    do {
        has_deleted_images = false;
        image_id = FindOrInsertImage(info, gpu_addr);
        delete_state |= has_deleted_images;
    } while (has_deleted_images);"""
new = """    ImageId image_id{};
    // NX2_RT_PADDED (scripts/apply-nx2-rt-padded-reuse.py): a render target whose width is only
    // padded past a texture image at the same address draws into that texture image.
    const auto nx2_padded_texture = [&]() -> ImageId {
        static const bool enabled = [] {
            // Off by default since build 204 washed Ocarina's fog and light out white.
            const char* value = std::getenv("EDEN_NX2_RT_PADDED_REUSE");
            return value != nullptr && value[0] == '1';
        }();
        if (!enabled || info.type != ImageType::e2D) {
            return ImageId{};
        }
        const size_t guest_size = CalculateGuestSizeInBytes(info);
        const std::optional<DAddr> cpu_addr = ImageCpuAddress(gpu_addr, guest_size);
        if (!cpu_addr) {
            return ImageId{};
        }
        ImageId found{};
        u64 found_tick = 0;
        ForEachImageInRegion(*cpu_addr, guest_size, [&](ImageId existing_id, ImageBase& existing) {
            const ImageInfo& other = existing.info;
            if (True(existing.flags & ImageFlagBits::Remapped) ||
                False(existing.flags & ImageFlagBits::Strong) || existing.gpu_addr != gpu_addr ||
                other.type != ImageType::e2D || other.format != info.format ||
                other.num_samples != info.num_samples || other.size.height != info.size.height ||
                other.size.width >= info.size.width ||
                other.resources.layers != info.resources.layers ||
                other.block.width != info.block.width || other.block.height != info.block.height ||
                other.block.depth != info.block.depth ||
                (info.resources.layers > 1 && other.layer_stride != info.layer_stride) ||
                !IsBlockLinearSizeCompatible(other, info, 0, 0, false)) {
                return false;
            }
            if (!found || existing.modification_tick > found_tick) {
                found = existing_id;
                found_tick = existing.modification_tick;
            }
            return false;
        });
        if (found) {
            static std::atomic<u32> logged{0};
            if (logged.fetch_add(1, std::memory_order_relaxed) < 24) {
                const ImageInfo& other = slot_images[found].info;
                LOG_INFO(HW_GPU,
                         "NX2_RT_PADDED {}x{} {} render target at {:#x} draws into the {}x{} "
                         "texture image there",
                         info.size.width, info.size.height, info.format, gpu_addr,
                         other.size.width, other.size.height);
            }
        }
        return found;
    };
    bool delete_state = has_deleted_images;
    do {
        has_deleted_images = false;
        image_id = nx2_padded_texture();
        if (!image_id) {
            image_id = FindOrInsertImage(info, gpu_addr);
        }
        delete_state |= has_deleted_images;
    } while (has_deleted_images);"""
if text.count(old) != 1:
    sys.exit(f"texture_cache.h: FindRenderTargetView lookup not found ({text.count(old)} matches)")
text = text.replace(old, new)
if "#include <atomic>\n" not in text:
    at = text.index("\n#include ") + 1
    text = text[:at] + "#include <atomic>\n" + text[at:]
path.write_text(text)
print("texture_cache.h: render targets reuse padded texture images (NX2_RT_PADDED)")
