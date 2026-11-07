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


def replace_optional(file: Path, old: str, new: str, label: str) -> None:
    """For diagnostics that depend on a block upstream may have reshaped."""
    data = file.read_text()
    if old not in data and new not in data:
        print(f"Skipped: {label} (block not present)")
        return
    replace_required(file, old, new, label)


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
# Old DKB boot diagnostic; newer Lichii reshaped OpenCurrentProcess (LayeredFS for NUPs).
if old_open in romfs_cpp.read_text() or new_open in romfs_cpp.read_text():
    replace_required(romfs_cpp, old_open, new_open, "DKB effective RomFS mount probe")
else:
    print("Skipped: DKB effective RomFS mount probe (upstream reshaped OpenCurrentProcess)")

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
                    // Newer SDKs may carry ZBIC (a modified zstd frame),
                    // with a short prefix before its magic. Detect the frame
                    // in the first 32 bytes, then try LZ4 if it is absent or
                    // the ZBIC decoder reports an unexpected size.
                    const auto decode = [&]() -> int {
                        constexpr u32 ZbicMagic = 0x4349425A;
                        std::size_t zbic_offset = source_size;
                        for (std::size_t at = 0; at + sizeof(u32) <= source_size &&
                                                 at <= 32; ++at) {
                            u32 magic{};
                            std::memcpy(&magic, source_data.data() + at, sizeof(magic));
                            if (magic == ZbicMagic) {
                                zbic_offset = at;
                                break;
                            }
                        }
                        if (zbic_offset < source_size || nso_header.IsZBICCompressed()) {
                            const auto start = zbic_offset < source_size ? zbic_offset : 0;
                            const int zbic_result = Common::Compression::DecompressDataZBIC(
                                std::span<u8>{decoded_data}.first(decoded_size),
                                std::span<const u8>{source_data}.subspan(
                                    start, source_size - start));
                            if (zbic_result == static_cast<int>(decoded_size)) {
                                return zbic_result;
                            }
                        }
                        return Common::Compression::DecompressDataLZ4(
                            decoded_data.data(), decoded_size,
                            source_data.data(), source_size);
                    };

                    int result = decode();
                    // Some Switch 2 NSOs place text at +0x108 rather than
                    // +0x100. Only probe that documented adjacent offset for
                    // segment zero, and only accept an exact-size decode.
                    if (result != static_cast<int>(decoded_size) && i == 0 &&
                        (source_offset == sizeof(NSOHeader) ||
                         source_offset == sizeof(NSOHeader) + 8)) {
                        const std::size_t alt_offset = source_offset == sizeof(NSOHeader)
                            ? sizeof(NSOHeader) + 8 : sizeof(NSOHeader);
                        if (alt_offset <= file_size &&
                            source_size <= file_size - alt_offset &&
                            nso_file.Read(source_data.data(), source_size, alt_offset) ==
                                source_size) {
                            result = decode();
                            if (result == static_cast<int>(decoded_size)) {
                                LOG_WARNING(Loader,
                                            "NSO {} segment 0 decoded at alternate text "
                                            "offset {:#x} (header offset {:#x})",
                                            nso_file.GetName(), alt_offset, source_offset);
                            }
                        }
                    }
                    if (result != static_cast<int>(decoded_size)) {
                        LOG_ERROR(Loader,
                                  "NSO {} segment {} decompression failed: got={} "
                                  "expected={} input={} offset={:#x} flags={:#x} "
                                  "(module not loaded)",
                                  nso_file.GetName(), i, result, decoded_size,
                                  source_size, source_offset, static_cast<u32>(nso_header.flags));
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

# Build 63: the Build 62 log shows 41,000+ unmapped writes when Dynarmic
# ignores memory aborts. For DKB only, stop at the first invalid access and
# record guest PC, LR, SP and key registers. No other title's settings change.
dynarmic = root / "src/core/arm/dynarmic/arm_dynarmic_64.cpp"
replace_required(
    dynarmic,
    "    , m_check_memory_access{m_debugger_enabled || !Settings::values.cpuopt_ignore_memory_aborts.GetValue()}",
    """    , m_check_memory_access{m_debugger_enabled ||
                            !Settings::values.cpuopt_ignore_memory_aborts.GetValue() ||
                            (process != nullptr && process->IsSwitch2() &&
                             process->GetProgramId() == 0x040074A01BF12000ULL)}""",
    "Strict Dynarmic memory accesses for DKB only",
)
dynarmic_h = root / "src/core/arm/dynarmic/arm_dynarmic_64.h"
replace_required(
    dynarmic_h,
    "    Kernel::KProcess* m_process{};",
    "    Kernel::KProcess* m_process{};\n    bool m_nx2_dkb_fault_reported{};",
    "One-shot DKB guest memory fault flag",
)
replace_required(
    dynarmic,
    """    if (!m_memory.IsValidVirtualAddressRange(addr, size)) {
        LOG_CRITICAL(Core_ARM, "Stopping execution due to unmapped memory access at {:#x}",
                        addr);
        m_parent.m_jit->HaltExecution(PrefetchAbort);
        return false;
    }""",
    """    if (!m_memory.IsValidVirtualAddressRange(addr, size)) {
        if (m_process != nullptr && m_process->IsSwitch2() &&
            m_process->GetProgramId() == 0x040074A01BF12000ULL) {
            if (!m_nx2_dkb_fault_reported) {
                m_nx2_dkb_fault_reported = true;
                LOG_CRITICAL(Core_ARM,
                             "NX2_DKB_FIRST_FAULT kind={} addr={:#x} size={} "
                             "pc={:#x} lr={:#x} sp={:#x} x0={:#x} x1={:#x} x8={:#x}",
                             static_cast<u32>(type), addr, size,
                             m_parent.m_jit->GetPC(),
                             m_parent.m_jit->GetRegister(30),
                             m_parent.m_jit->GetSP(),
                             m_parent.m_jit->GetRegister(0),
                             m_parent.m_jit->GetRegister(1),
                             m_parent.m_jit->GetRegister(8));
            }
        } else {
            LOG_CRITICAL(Core_ARM,
                         "Stopping execution due to unmapped memory access at {:#x}", addr);
        }
        m_parent.m_jit->HaltExecution(PrefetchAbort);
        return false;
    }""",
    "Halt and trace first invalid DKB guest memory access",
)

# Log the root-level RomFS directory names and update provenance. This
# distinguishes a base-only image from an update registration/mount problem.
replace_required(
    romfs_cpp,
    '#include "core/file_sys/romfs.h"',
    '#include <string>\n#include "core/file_sys/romfs.h"',
    "RomFS layout diagnostic string include",
)
replace_optional(
    romfs_cpp,
    """        LOG_WARNING(Service_FS,
                    "NX2_ROMFS_PROBE DKB base_bytes={:#x} mounted_bytes={:#x} """,
    """        std::string top_level_dirs;
        if (root != nullptr) {
            std::size_t count{};
            for (const auto& dir : root->GetSubdirectories()) {
                if (count++ == 12) {
                    top_level_dirs += ",...";
                    break;
                }
                if (!top_level_dirs.empty()) {
                    top_level_dirs += ",";
                }
                top_level_dirs += dir->GetName().substr(0, 48);
            }
        }
        const auto available_update = content_provider.GetEntry(
            GetUpdateTitleID(current_process_title_id), ContentRecordType::Program);
        LOG_WARNING(Service_FS,
                    "NX2_ROMFS_LAYOUT DKB updatable={} packed_update={} "
                    "registered_update={} top_dirs=[{}]",
                    updatable, packed_update_raw != nullptr,
                    available_update != nullptr, top_level_dirs);
        LOG_WARNING(Service_FS,
                    "NX2_ROMFS_PROBE DKB base_bytes={:#x} mounted_bytes={:#x} """,
    "One-shot DKB effective RomFS root directory and update status",
)

# A repeatable NSO decode failure at +0x10a needs a 4-byte byte signature,
# not speculative additional codec/offset fallbacks.
data = nso_file.read_text()
nso_failure = """                    if (result != static_cast<int>(decoded_size)) {
                        LOG_ERROR(Loader,
                                  "NSO {} segment {} decompression failed: got={} """""
nso_probe = """                    if (result != static_cast<int>(decoded_size)) {
                        const auto byte_at = [&](std::size_t at) -> u32 {
                            return at < source_size ? static_cast<u32>(source_data[at]) : 0u;
                        };
                        LOG_ERROR(Loader,
                                  "NX2_NSO_INPUT {} segment={} first4={:02x}{:02x}{:02x}{:02x} "
                                  "last4={:02x}{:02x}{:02x}{:02x} input_end={:#x} file_size={:#x}",
                                  nso_file.GetName(), i,
                                  byte_at(0), byte_at(1), byte_at(2), byte_at(3),
                                  byte_at(source_size > 3 ? source_size - 4 : 0),
                                  byte_at(source_size > 2 ? source_size - 3 : 0),
                                  byte_at(source_size > 1 ? source_size - 2 : 0),
                                  byte_at(source_size > 0 ? source_size - 1 : 0),
                                  source_offset + source_size, file_size);
                        LOG_ERROR(Loader,
                                  "NSO {} segment {} decompression failed: got={} """""
if "NX2_NSO_INPUT" not in data:
    if nso_failure not in data:
        raise SystemExit("Could not find NSO failure logging insertion point")
    data = data.replace(nso_failure, nso_probe, 1)
    nso_file.write_text(data)
    print("Applied: NSO failed-decompression byte signature diagnostics")

print("NX2 content guards installed")
