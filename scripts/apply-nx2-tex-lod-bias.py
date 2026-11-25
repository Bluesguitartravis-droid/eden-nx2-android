#!/usr/bin/env python3
"""EDEN_NX2_TEX_LOD_BIAS=<0..4> (read at each game boot from the switches file): textures the game
loaded are sampled that many mip levels coarser (sampler mipLodBias + bias), so the GPU reads
smaller texture levels: less memory bandwidth, softer textures. Images the GPU wrote (render
targets, bloom and blur chains, reflections) keep their own bias, as with the Ocarina FSR bias.
Samplers made before the value was read keep theirs, so set it before the game starts."""
from pathlib import Path
import sys

root = Path(sys.argv[1]).resolve() / "src/video_core/renderer_vulkan"
h = root / "vk_texture_cache.h"
c = root / "vk_texture_cache.cpp"
ht = h.read_text()
ct = c.read_text()


def sub(text, old, new, name):
    if text.count(old) != 1:
        sys.exit(f"apply-nx2-tex-lod-bias: expected one {old[:70]!r} in {name}, found {text.count(old)}")
    return text.replace(old, new)


ht = sub(ht, '''        bool drop_lod_bias; ///< the image was written by the GPU: no Ocarina FSR LOD bias
''', '''        bool drop_lod_bias; ///< the image was written by the GPU: no Ocarina FSR LOD bias
        bool drop_user_lod_bias; ///< the image was written by the GPU: no EDEN_NX2_TEX_LOD_BIAS
''', "vk_texture_cache.h")
ht = sub(ht, '''    f32 added_lod_bias{}; ///< Ocarina FSR part of base_ci.mipLodBias
''', '''    f32 added_lod_bias{}; ///< Ocarina FSR part of base_ci.mipLodBias
    f32 user_lod_bias{}; ///< EDEN_NX2_TEX_LOD_BIAS part of base_ci.mipLodBias
''', "vk_texture_cache.h")
ht = sub(ht, '''    [[nodiscard]] f32 OcarinaFsrLodBias();
    std::optional<f32> ocarina_fsr_lod_bias;
''', '''    [[nodiscard]] f32 OcarinaFsrLodBias();
    std::optional<f32> ocarina_fsr_lod_bias;
    [[nodiscard]] f32 Nx2TexLodBias();
    std::optional<f32> nx2_tex_lod_bias;
''', "vk_texture_cache.h")

ct = sub(ct, '''f32 TextureCacheRuntime::OcarinaFsrLodBias() {''', '''f32 TextureCacheRuntime::Nx2TexLodBias() {
    if (!nx2_tex_lod_bias) {
        const std::string value = VideoCommon::Nx2LiveSwitch("EDEN_NX2_TEX_LOD_BIAS");
        f32 bias = value.empty() ? 0.0f : std::strtof(value.c_str(), nullptr);
        if (!std::isfinite(bias)) {
            bias = 0.0f;
        }
        bias = std::clamp(bias, 0.0f, 4.0f);
        nx2_tex_lod_bias = bias;
        if (bias != 0.0f) {
            LOG_INFO(Render_Vulkan, "NX2_TEX_LOD_BIAS: the game's textures sampled {:.2f} mip levels coarser", bias);
        }
    }
    return *nx2_tex_lod_bias;
}

f32 TextureCacheRuntime::OcarinaFsrLodBias() {''', "vk_texture_cache.cpp")
ct = sub(ct, '''        added_lod_bias = biased - lod_bias;
        lod_bias = biased;
    }
    if (!device.SupportsSamplerMipLodBias()) {
        lod_bias = 0.0f;
        added_lod_bias = 0.0f;
    }''', '''        added_lod_bias = biased - lod_bias;
        lod_bias = biased;
    }
    if (const f32 user_bias = runtime.Nx2TexLodBias();
        user_bias != 0.0f && tsc.mipmap_filter != TextureMipmapFilter::None) {
        const f32 biased = std::clamp(lod_bias + user_bias, -15.0f, 15.0f);
        user_lod_bias = biased - lod_bias;
        lod_bias = biased;
    }
    if (!device.SupportsSamplerMipLodBias()) {
        lod_bias = 0.0f;
        added_lod_bias = 0.0f;
        user_lod_bias = 0.0f;
    }''', "vk_texture_cache.cpp")
ct = sub(ct, '''    if (needs_swizzle_mapping && !key.drop_custom_border && !image_view.HasIdentitySwizzle()) {''', '''    if (user_lod_bias != 0.0f) {
        const Image* const image = image_view.BackingImage();
        key.drop_user_lod_bias = image != nullptr && True(image->flags & ImageFlagBits::GpuModified);
    }
    if (needs_swizzle_mapping && !key.drop_custom_border && !image_view.HasIdentitySwizzle()) {''', "vk_texture_cache.cpp")
ct = sub(ct, '''    if (key.drop_lod_bias) {
        create_info.mipLodBias -= added_lod_bias;
    }''', '''    if (key.drop_lod_bias) {
        create_info.mipLodBias -= added_lod_bias;
    }
    if (key.drop_user_lod_bias) {
        create_info.mipLodBias -= user_lod_bias;
    }''', "vk_texture_cache.cpp")
h.write_text(ht)
c.write_text(ct)
print("apply-nx2-tex-lod-bias: EDEN_NX2_TEX_LOD_BIAS added")
