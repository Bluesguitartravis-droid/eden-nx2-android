#!/usr/bin/env python3
"""Skip the CPU upload of an image whose pixel format is not a real format.

Ocarina of Time, right after its opening video (builds 139 and 142, Qualcomm driver): an image
with an invalid pixel format reached the upload, and BitsPerBlock() hit UNREACHABLE (abort). In
139 the same image asked for a 0-byte staging buffer first. Leave such an image's contents alone
(it has no defined layout) instead of aborting.
"""
from pathlib import Path
import sys

f = Path(sys.argv[1]) / "src/video_core/texture_cache/texture_cache.h"
t = f.read_text()
old = """void TextureCache<P>::RefreshContents(Image& image, ImageId image_id) {
    if (False(image.flags & ImageFlagBits::CpuModified)) {
        // Only upload modified images
        return;
    }
"""
new = old + """    if (static_cast<std::size_t>(image.info.format) >= VideoCore::Surface::MaxPixelFormat) {
        // NX2: no defined layout to unswizzle (BitsPerBlock would abort).
        image.flags &= ~ImageFlagBits::CpuModified;
        static u32 nx2_invalid_logged{};
        if (nx2_invalid_logged++ < 8) {
            LOG_WARNING(HW_GPU, "Skipping the upload of image at {:#x} with invalid format {}",
                        image.gpu_addr, static_cast<u32>(image.info.format));
        }
        return;
    }
"""
if "NX2: no defined layout to unswizzle" in t:
    print("invalid-format upload guard already applied"); sys.exit(0)
if old not in t:
    raise SystemExit("texture_cache.h: RefreshContents shape changed")
f.write_text(t.replace(old, new, 1))
print("uploads of images with an invalid pixel format skipped")
