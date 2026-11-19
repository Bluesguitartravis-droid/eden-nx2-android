#!/usr/bin/env python3
"""NX2 copy census: which image copies end Ocarina's render passes.

Build 198 on the Odin, Ocarina in Hyrule Field: 5,664 render passes a second at ~12 fps, 2,685 of
them ended by TextureCacheRuntime::CopyImage (vk_texture_cache.cpp), and the frame trace put
~35 ms of each ~80 ms GPU frame between draws. This counts every runtime image copy by why the
texture cache made it (alias sync, image join, 2D copy into a 3D slice, other) and by source and
destination format and size, and logs the top ones every 10 s beside the scheduler's render pass
report: "NX2_COPIES ...". Logging only; no behaviour changes.

Usage: apply-nx2-copy-census.py <upstream source root>
"""
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
vc = root / "src/video_core"

HEADER = r'''// SPDX-License-Identifier: GPL-3.0-or-later
// NX2 copy census (scripts/apply-nx2-copy-census.py): counts the texture cache's image copies by
// reason, format and size, and logs the top ones every 10 s.
#pragma once

#include <algorithm>
#include <array>
#include <cstdint>
#include <mutex>
#include <string>
#include <vector>

#include <fmt/format.h>

#include "common/logging.h"
#include "video_core/texture_cache/formatter.h"

namespace VideoCommon::Nx2CopyCensus {

enum class Reason : std::uint8_t { Other, Alias, Join, Slice3D };

inline const char* ReasonName(Reason reason) {
    switch (reason) {
    case Reason::Alias:
        return "alias";
    case Reason::Join:
        return "join";
    case Reason::Slice3D:
        return "2d-into-3d";
    default:
        return "other";
    }
}

inline thread_local Reason g_reason = Reason::Other;

struct Scope {
    Reason previous;
    explicit Scope(Reason reason) : previous{g_reason} {
        g_reason = reason;
    }
    ~Scope() {
        g_reason = previous;
    }
};

struct Key {
    Reason reason;
    bool reinterpret;
    VideoCore::Surface::PixelFormat src_format;
    VideoCore::Surface::PixelFormat dst_format;
    std::uint32_t src_width, src_height, src_layers;
    std::uint32_t dst_width, dst_height, dst_layers;
    std::uint32_t copy_width, copy_height;
    bool operator==(const Key&) const = default;
};

struct Entry {
    Key key;
    std::uint64_t count;
    std::uint64_t regions;
};

inline std::mutex g_mutex;
inline std::vector<Entry> g_entries;
inline std::uint64_t g_overflow;

inline void Note(const Key& key, std::size_t regions) {
    std::scoped_lock lock{g_mutex};
    for (Entry& entry : g_entries) {
        if (entry.key == key) {
            ++entry.count;
            entry.regions += regions;
            return;
        }
    }
    if (g_entries.size() >= 512) {
        ++g_overflow;
        return;
    }
    g_entries.push_back(Entry{key, 1, regions});
}

template <typename ImageT, typename CopiesT>
inline void NoteCopy(const ImageT& dst, const ImageT& src, const CopiesT& copies, bool reinterpret) {
    if (copies.empty()) {
        return;
    }
    Note(Key{
             .reason = g_reason,
             .reinterpret = reinterpret,
             .src_format = src.info.format,
             .dst_format = dst.info.format,
             .src_width = src.info.size.width,
             .src_height = src.info.size.height,
             .src_layers = static_cast<std::uint32_t>(src.info.resources.layers),
             .dst_width = dst.info.size.width,
             .dst_height = dst.info.size.height,
             .dst_layers = static_cast<std::uint32_t>(dst.info.resources.layers),
             .copy_width = static_cast<std::uint32_t>(copies[0].extent.width),
             .copy_height = static_cast<std::uint32_t>(copies[0].extent.height),
         },
         copies.size());
}

inline void Report(double seconds) {
    std::vector<Entry> entries;
    std::uint64_t overflow = 0;
    {
        std::scoped_lock lock{g_mutex};
        entries.swap(g_entries);
        overflow = g_overflow;
        g_overflow = 0;
    }
    if (entries.empty() || seconds <= 0.0) {
        return;
    }
    std::array<std::uint64_t, 4> by_reason{};
    std::uint64_t total = 0;
    for (const Entry& entry : entries) {
        by_reason[static_cast<std::size_t>(entry.key.reason)] += entry.count;
        total += entry.count;
    }
    LOG_INFO(Render_Vulkan,
             "NX2_COPIES {:.0f}/s image copies: alias {:.0f}/s, join {:.0f}/s, 2d-into-3d {:.0f}/s, "
             "other {:.0f}/s; {} kinds{}",
             static_cast<double>(total) / seconds, static_cast<double>(by_reason[1]) / seconds,
             static_cast<double>(by_reason[2]) / seconds, static_cast<double>(by_reason[3]) / seconds,
             static_cast<double>(by_reason[0]) / seconds, entries.size(),
             overflow != 0 ? fmt::format(", {} not counted (table full)", overflow) : std::string{});
    std::sort(entries.begin(), entries.end(),
              [](const Entry& a, const Entry& b) { return a.count > b.count; });
    for (std::size_t i = 0; i < entries.size() && i < 12; ++i) {
        const Key& k = entries[i].key;
        LOG_INFO(Render_Vulkan,
                 "NX2_COPIES #{} {:.1f}/s {}{}: {} {}x{}x{} -> {} {}x{}x{}, region {}x{}, {:.1f} regions each",
                 i + 1, static_cast<double>(entries[i].count) / seconds, ReasonName(k.reason),
                 k.reinterpret ? " reinterpret" : "", k.src_format, k.src_width, k.src_height,
                 k.src_layers, k.dst_format, k.dst_width, k.dst_height, k.dst_layers, k.copy_width,
                 k.copy_height,
                 static_cast<double>(entries[i].regions) / static_cast<double>(entries[i].count));
    }
}

} // namespace VideoCommon::Nx2CopyCensus
'''


def patch(path, edits, marker):
    text = path.read_text()
    if marker in text:
        print(f"{path.name}: already patched")
        return
    for old, new in edits:
        if text.count(old) != 1:
            sys.exit(f"{path.name}: expected one match for {old[:70]!r}, found {text.count(old)}")
        text = text.replace(old, new)
    path.write_text(text)
    print(f"{path.name}: patched")


(vc / "nx2_copy_census.h").write_text(HEADER)

INC = '#include "video_core/nx2_copy_census.h"\n'

patch(vc / "texture_cache/texture_cache.h", [
    ("    const auto& resolution = Settings::values.resolution_info;\n"
     "    for (const AliasedImage* const aliased : aliased_images) {",
     "    const VideoCommon::Nx2CopyCensus::Scope nx2_census{VideoCommon::Nx2CopyCensus::Reason::Alias};\n"
     "    const auto& resolution = Settings::values.resolution_info;\n"
     "    for (const AliasedImage* const aliased : aliased_images) {"),
    ("    for (size_t index = 0; index < join_copies_to_do.size(); ++index) {\n"
     "        const JoinCopy& copy_object = join_copies_to_do[index];",
     "    const VideoCommon::Nx2CopyCensus::Scope nx2_census{VideoCommon::Nx2CopyCensus::Reason::Join};\n"
     "    for (size_t index = 0; index < join_copies_to_do.size(); ++index) {\n"
     "        const JoinCopy& copy_object = join_copies_to_do[index];"),
    ("    runtime.CopyImage(slot_images[dst_slice->id], slot_images[src_slice->id],",
     "    const VideoCommon::Nx2CopyCensus::Scope nx2_census{VideoCommon::Nx2CopyCensus::Reason::Slice3D};\n"
     "    runtime.CopyImage(slot_images[dst_slice->id], slot_images[src_slice->id],"),
    ("#pragma once\n", "#pragma once\n" + INC),
], "Nx2CopyCensus")

patch(vc / "renderer_vulkan/vk_texture_cache.cpp", [
    ("void TextureCacheRuntime::CopyImage(Image& dst, Image& src,\n"
     "                                    std::span<const VideoCommon::ImageCopy> copies) {\n"
     "    boost::container::small_vector<VideoCommon::ImageCopy, 16> host_copies;\n"
     "    copies = HostImageCopies(dst, src, copies, host_copies);\n"
     "    if (copies.empty())\n"
     "        return;\n",
     "void TextureCacheRuntime::CopyImage(Image& dst, Image& src,\n"
     "                                    std::span<const VideoCommon::ImageCopy> copies) {\n"
     "    boost::container::small_vector<VideoCommon::ImageCopy, 16> host_copies;\n"
     "    copies = HostImageCopies(dst, src, copies, host_copies);\n"
     "    if (copies.empty())\n"
     "        return;\n"
     "    VideoCommon::Nx2CopyCensus::NoteCopy(dst, src, copies,\n"
     "                                         BytesPerBlock(src.info.format) != BytesPerBlock(dst.info.format));\n"),
    ('#include "common/memory_detect.h"\n', '#include "common/memory_detect.h"\n' + INC),
], "Nx2CopyCensus")

patch(vc / "renderer_vulkan/vk_scheduler.cpp", [
    ("    std::sort(ends.rbegin(), ends.rend());\n"
     "    for (size_t i = 0; i < ends.size() && i < 16; ++i) {",
     "    VideoCommon::Nx2CopyCensus::Report(seconds);\n"
     "    std::sort(ends.rbegin(), ends.rend());\n"
     "    for (size_t i = 0; i < ends.size() && i < 16; ++i) {"),
    ("#include <source_location>\n", "#include <source_location>\n" + INC),
], "Nx2CopyCensus")
print("NX2 copy census added")
