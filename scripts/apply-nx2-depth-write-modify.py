#!/usr/bin/env python3
"""A depth buffer bound for a draw that writes neither depth nor stencil is not marked modified.

Build 211, Ocarina at Lon Lon Ranch at night: ~230 render pass ends a frame at the alias copy in
TextureCacheRuntime::CopyImage. NX2_COPIES: D32_FLOAT 432x240 -> 427x240 (texture) ~2,900/s and
864x480 -> 854x480 ~690/s. The game draws (soft particles, fog volumes) with its padded depth
buffer bound for testing while sampling the same memory as a 427-wide texture. Every draw marked
the bound depth image modified, so the next draw's texture saw a newer alias and copied it again,
ending the render pass each time, although nothing had written the depth.

UpdateRenderTargets now marks the depth image modified only for clears and for draws with depth
writes on or a stencil test that can write. EDEN_NX2_DEPTH_WRITE_MODIFY=0 restores the old marking.

Usage: apply-nx2-depth-write-modify.py <upstream source root>
"""
import pathlib
import sys

path = pathlib.Path(sys.argv[1]) / "src/video_core/texture_cache/texture_cache.h"
text = path.read_text()
if "NX2 depth write modify" in text:
    print("texture_cache.h: already patched")
    sys.exit(0)

anchor = """    clearing_render_targets = is_clear;
    drawing_render_targets = !is_clear;
    auto& flags = maxwell3d->dirty.flags;
"""
if text.count(anchor) != 1:
    sys.exit("texture_cache.h: UpdateRenderTargets anchor not found")
text = text.replace(anchor, """    clearing_render_targets = is_clear;
    drawing_render_targets = !is_clear;
    auto& flags = maxwell3d->dirty.flags;
    // NX2 depth write modify: a draw that cannot write depth or stencil leaves the depth image as
    // it was, so a texture aliasing it stays current (no copy, no render pass end).
    const bool nx2_depth_modified = [&] {
        static const bool enabled = [] {
            const char* value = std::getenv("EDEN_NX2_DEPTH_WRITE_MODIFY");
            return value == nullptr || value[0] != '0';
        }();
        if (is_clear || !enabled) {
            return true;
        }
        const auto& regs = maxwell3d->regs;
        if (regs.depth_write_enabled != 0) {
            return true;
        }
        return regs.stencil_enable != 0 &&
               (regs.stencil_front_mask != 0 ||
                (regs.stencil_two_side_enable != 0 && regs.stencil_back_mask != 0));
    }();
""")
old = "PrepareImageView(depth_buffer_id, true, is_clear && IsFullClear(depth_buffer_id));"
if text.count(old) != 2:
    sys.exit(f"texture_cache.h: expected two depth PrepareImageView calls, found {text.count(old)}")
text = text.replace(old, "PrepareImageView(depth_buffer_id, nx2_depth_modified,\n"
                         "                     is_clear && IsFullClear(depth_buffer_id));")
path.write_text(text)
print("texture_cache.h: depth images marked modified only when the draw can write them")
