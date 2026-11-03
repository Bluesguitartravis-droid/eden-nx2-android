#!/usr/bin/env python3
"""Build 60: inspect effective NX2 RomFS mounts and fail closed on invalid NSO data.

These are targeted compatibility diagnostics. They never fabricate title
resources or alter guest service return values.
"""
from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: apply-nx2-content-guards.py <upstream-dir>")
root = Path(sys.argv[1]).resolve()


def replace_required(file: Path, old: str, new: str, label: str) -> None:
    data = file.read_text()
    if new in data:
        print(f"Already applied: {label}")
        return
    if old not in data:
        raise SystemExit(f"Could not find {label} in {file}")
    file.write_text(data.replace(old, new, 1))
    print(f"Applied: {label}")


# The DKB failure resembles the one in Eglantine issue #335: a base-only RomFS
# can contain Voice/Voxel/XLink yet lack the updated System/HeapSize and RSDB.
# Probe the *mounted* (potentially patched) RomFS, not merely an NSP's filename.
romfs_h = root / "src/core/file_sys/romfs_factory.h"
replace_required(
    romfs_h, "#include <memory>", "#include <atomic>\n#include <memory>",
    "RomFSFactory atomic diagnostic guard include",
)
replace_required(
    romfs_h, "    bool updatable;\n", 
    "    bool updatable;\n    mutable std::atomic_bool nx2_romfs_reported{false};\n",
    "RomFSFactory per-instance one-shot diagnostic",
)

romfs_cpp = root / "src/core/file_sys/romfs_factory.cpp"
replace_required(
    romfs_cpp, '#include "core/file_sys/romfs_factory.h"',
    '#include "core/file_sys/romfs.h"\n#include "core/file_sys/romfs_factory.h"',
    "RomFS VFS introspection include",
)
old_open = """VirtualFile RomFSFactory::OpenCurrentProcess(u64 current_process_title_id) const {
    if (!updatable) {
        return file;
    }

    const auto type = ContentRecordType::Program;
    const auto nca = content_provider.GetEntry(current_process_title_id, type);
    const PatchManager patch_manager{current_process_title_id, filesystem_controller,
                                     content_provider};
    return patch_manager.PatchRomFS(nca.get(), file, ContentRecordType::Program, packed_update_raw);
}"""
new_open = """VirtualFile RomFSFactory::OpenCurrentProcess(u64 current_process_title_id) const {
    VirtualFile mounted = file;
    if (updatable) {
        const auto type = ContentRecordType::Program;
        const auto nca = content_provider.GetEntry(current_process_title_id, type);
        const PatchManager patch_manager{current_process_title_id, filesystem_controller,
                                         content_provider};
        mounted = patch_manager.PatchRomFS(nca.get(), file, type, packed_update_raw);
    }

    // DKB Build 57 reached the same null guest branch despite successful save-data IPC.
    // Issue #335 reports the same signature when the update RomFS is missing.
    // Reading only RomFS directory metadata here tells us what is actually mounted
    // without reading the title's (possibly multi-GB) file contents.
    constexpr u64 DkbTitleId = 0x040074A01BF12000ULL;
    if (current_process_title_id == DkbTitleId &&
        !nx2_romfs_reported.exchange(true, std::memory_order_relaxed)) {
        VirtualDir root{};
        u64 header_size{};
        if (mounted != nullptr && mounted->ReadObject(&header_size) == sizeof(header_size) &&
            header_size == 0x50) {
            root = ExtractRomFS(mounted);
        }
        const auto system_dir = root != nullptr ? root->GetSubdirectory("System") : nullptr;
        const auto heap_dir = system_dir != nullptr
                                  ? system_dir->GetSubdirectory("HeapSize") : nullptr;
        const auto heap_file = system_dir != nullptr ? system_dir->GetFile("HeapSize") : nullptr;
        const auto rsdb_dir = root != nullptr ? root->GetSubdirectory("RSDB") : nullptr;
        LOG_WARNING(Service_FS,
                    "NX2_ROMFS_PROBE DKB base_bytes={:#x} mounted_bytes={:#x} "
                    "romfs_valid={} System={} System/HeapSize={} RSDB={}",
                    file != nullptr ? file->GetSize() : 0,
                    mounted != nullptr ? mounted->GetSize() : 0,
                    root != nullptr, system_dir != nullptr,
                    heap_dir != nullptr || heap_file != nullptr, rsdb_dir != nullptr);
    }
    return mounted;
}"""
replace_required(romfs_cpp, old_open, new_open, "DKB effective RomFS mount probe")

# The original NSO loader asserted when LZ4/ZBIC failed, then still copied the
# decompression buffer into guest code. It also used compressed-size headers to
# read uncompressed sections and never checked short reads. Fail cleanly with
# module/section details instead of executing invalid/zero-filled code.
nso_file = root / "src/core/loader/nso.cpp"
data = nso_file.read_text()
marker = "// NX2 guarded NSO segment reads"
if marker not in data:
    start_marker = "    {\n        std::vector<u8> compressed_data("
    end_marker = "\n    if (should_pass_arguments"
    start = data.find(start_marker)
    end = data.find(end_marker, start)
    if start < 0 or end < 0:
        raise SystemExit(f"Could not find NSO decode loop in {nso_file}")
    original = data[start:end]
    if "ASSERT(r > 0)" not in original or "ASSERT(r == int(nso_header.segments[i].size))" not in original:
        raise SystemExit("NSO decode loop shape differs; refusing an unsafe patch")
    replacement = """    {
        // NX2 guarded NSO segment reads: malformed or unsupported compressed
        // segments must not silently become executable zero-filled memory.
        const auto max_compressed = static_cast<std::size_t>(
            *std::ranges::max_element(nso_header.segments_compressed_size));
        const auto max_decoded = static_cast<std::size_t>(
            std::ranges::max_element(nso_header.segments, [](const auto& a, const auto& b) {
                return a.size < b.size;
            })->size);
        std::vector<u8> source_data((std::max)(max_compressed, max_decoded));
        std::vector<u8> decoded_data(max_decoded);

        for (std::size_t i = 0; i < nso_header.segments.size(); ++i) {
            const auto& segment = nso_header.segments[i];
            const std::size_t decoded_size = segment.size;
            const bool compressed = nso_header.IsSegmentCompressed(i);
            const std::size_t source_size =
                compressed ? static_cast<std::size_t>(nso_header.segments_compressed_size[i])
                           : decoded_size;
            const std::size_t source_offset = segment.offset;
            const std::size_t destination_offset = module_start + segment.location;
            const std::size_t file_size = nso_file.GetSize();

            if (source_offset > file_size || source_size > file_size - source_offset ||
                destination_offset > codeset.memory.size() ||
                decoded_size > codeset.memory.size() - destination_offset ||
                source_size > source_data.size() || decoded_size > decoded_data.size()) {
                LOG_ERROR(Loader,
                          "NSO {} segment {} has invalid range: off={:#x} input={:#x} "
                          "decoded={:#x} file={:#x}",
                          nso_file.GetName(), i, source_offset, source_size,
                          decoded_size, file_size);
                return std::nullopt;
            }

            if (decoded_size != 0) {
                const std::size_t read_count =
                    nso_file.Read(source_data.data(), source_size, source_offset);
                if (read_count != source_size) {
                    LOG_ERROR(Loader, "NSO {} segment {} short read: got={:#x} need={:#x}",
                              nso_file.GetName(), i, read_count, source_size);
                    return std::nullopt;
                }

                if (compressed) {
                    if (decoded_size > static_cast<std::size_t>(
                                           std::numeric_limits<int>::max()) ||
                        source_size > static_cast<std::size_t>(
                                          std::numeric_limits<int>::max())) {
                        LOG_ERROR(Loader, "NSO {} segment {} too large for decoder",
                                  nso_file.GetName(), i);
                        return std::nullopt;
                    }
                    const int result = nso_header.IsZBICCompressed()
                        ? Common::Compression::DecompressDataZBIC(
                              std::span<u8>{decoded_data}.first(decoded_size),
                              std::span<const u8>{source_data}.first(source_size))
                        : Common::Compression::DecompressDataLZ4(
                              decoded_data.data(), decoded_size,
                              source_data.data(), source_size);
                    if (result != static_cast<int>(decoded_size)) {
                        LOG_ERROR(Loader,
                                  "NSO {} segment {} {} decompression failed: got={} "
                                  "expected={} input={} (module not loaded)",
                                  nso_file.GetName(), i,
                                  nso_header.IsZBICCompressed() ? "ZBIC" : "LZ4",
                                  result, decoded_size, source_size);
                        return std::nullopt;
                    }
                    std::memcpy(codeset.memory.data() + destination_offset,
                                decoded_data.data(), decoded_size);
                } else {
                    std::memcpy(codeset.memory.data() + destination_offset,
                                source_data.data(), decoded_size);
                }
            }

            codeset.segments[i].addr = module_start + segment.location;
            codeset.segments[i].offset = module_start + segment.location;
            codeset.segments[i].size = segment.size;
        }
    }
"""
    data = data[:start] + replacement + data[end:]
    if "#include <limits>" not in data:
        data = data.replace("#include <cstring>", "#include <cstring>\n#include <limits>", 1)
    nso_file.write_text(data)
    print("Applied: guarded NSO decompression and short-read checks")
else:
    print("Already applied: guarded NSO decompression and short-read checks")

print("NX2 content guards installed")
