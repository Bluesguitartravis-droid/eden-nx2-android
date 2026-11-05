#!/usr/bin/env python3
from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: apply-eglantine-switch2-regressions.py <upstream-dir>")

root = Path(sys.argv[1]).resolve()

# ---------------------------------------------------------------------------
# 1. Detect Switch 2 from an embedded Program-NCA NPDM before kernel startup.
#    Eglantine's pinned IsSwitch2Program only knew NUP and loose ExeFS, so our
#    plaintext NSPs booted with the Switch 1 4 GiB / 19.2 MHz startup settings.
# ---------------------------------------------------------------------------
core = root / "src" / "core" / "core.cpp"
core_text = core.read_text()

if '#include "core/file_sys/content_archive.h"' not in core_text:
    anchor = '#include "core/file_sys/fs_filesystem.h"\n'
    if anchor not in core_text:
        print("NX2_PATCH_SKIP:", f"Could not find content-archive include anchor in {core}")
    core_text = core_text.replace(
        anchor,
        '#include "core/file_sys/content_archive.h"\n' + anchor,
        1,
    )

if '#include "core/file_sys/submission_package.h"' not in core_text:
    anchor = '#include "core/file_sys/savedata_factory.h"\n'
    if anchor not in core_text:
        print("NX2_PATCH_SKIP:", f"Could not find submission-package include anchor in {core}")
    core_text = core_text.replace(
        anchor,
        anchor + '#include "core/file_sys/submission_package.h"\n',
        1,
    )

old_switch2 = '''// Switch 2 titles boot as a loose ExeFS, with main.npdm next to main.
bool IsSwitch2Program(const FileSys::VirtualFile& file) {
    FileSys::ProgramMetadata metadata;
    if (FileSys::NupContainer::IsNupFile(file)) {
        FileSys::NupContainer nup{file};
        if (nup.GetStatus() != Loader::ResultStatus::Success) {
            return false;
        }
        std::optional<std::size_t> program;
        if (const auto base = nup.FindBasePackage();
            base && nup.GetExeFSDir(*base) != nullptr) {
            program = base;
        } else if (const auto update = nup.FindBestUpdatePackage();
                   update && nup.GetExeFSDir(*update) != nullptr) {
            program = update;
        }
        const auto exefs = program ? nup.GetExeFSDir(*program) : nullptr;
        const auto npdm = exefs != nullptr ? exefs->GetFile("main.npdm") : nullptr;
        return npdm != nullptr && metadata.Load(npdm) == Loader::ResultStatus::Success &&
               metadata.IsSwitch2();
    }
    const auto dir = file != nullptr ? file->GetContainingDirectory() : nullptr;
    const auto npdm = dir != nullptr ? dir->GetFile("main.npdm") : nullptr;
    return npdm != nullptr && metadata.Load(npdm) == Loader::ResultStatus::Success &&
           metadata.IsSwitch2();
}
'''

new_switch2 = '''bool IsSwitch2Npdm(const FileSys::VirtualFile& npdm) {
    if (npdm == nullptr) {
        return false;
    }

    FileSys::ProgramMetadata metadata;
    return metadata.Load(npdm) == Loader::ResultStatus::Success && metadata.IsSwitch2();
}

// Detect Switch 2 early enough to select its memory layout and 31.25 MHz counter.
// Support NUP, loose ExeFS, and plaintext/readable NSP Program NCAs.
bool IsSwitch2Program(const FileSys::VirtualFile& file) {
    if (file == nullptr) {
        return false;
    }

    if (FileSys::NupContainer::IsNupFile(file)) {
        FileSys::NupContainer nup{file};
        if (nup.GetStatus() != Loader::ResultStatus::Success) {
            return false;
        }
        std::optional<std::size_t> program;
        if (const auto base = nup.FindBasePackage();
            base && nup.GetExeFSDir(*base) != nullptr) {
            program = base;
        } else if (const auto update = nup.FindBestUpdatePackage();
                   update && nup.GetExeFSDir(*update) != nullptr) {
            program = update;
        }
        const auto exefs = program ? nup.GetExeFSDir(*program) : nullptr;
        return exefs != nullptr && IsSwitch2Npdm(exefs->GetFile("main.npdm"));
    }

    const auto dir = file->GetContainingDirectory();
    if (dir != nullptr && IsSwitch2Npdm(dir->GetFile("main.npdm"))) {
        return true;
    }

    if (Common::ToLower(file->GetExtension()) == "nsp") {
        FileSys::NSP nsp{file};
        if (nsp.GetStatus() != Loader::ResultStatus::Success) {
            return false;
        }

        if (nsp.IsExtractedType()) {
            const auto exefs = nsp.GetExeFS();
            return exefs != nullptr && IsSwitch2Npdm(exefs->GetFile("main.npdm"));
        }

        const u64 program_id = nsp.GetProgramTitleID();
        if (program_id == 0) {
            return false;
        }

        const auto program_nca = nsp.GetNCA(program_id, FileSys::ContentRecordType::Program);
        if (program_nca == nullptr ||
            program_nca->GetStatus() != Loader::ResultStatus::Success) {
            return false;
        }

        const auto exefs = program_nca->GetExeFS();
        if (exefs != nullptr && IsSwitch2Npdm(exefs->GetFile("main.npdm"))) {
            LOG_INFO(Core, "Switch 2 program detected from embedded NSP Program NCA NPDM ({})",
                     file->GetName());
            return true;
        }
    }

    return false;
}
'''

if "Switch 2 program detected from embedded NSP Program NCA NPDM" not in core_text:
    if old_switch2 not in core_text:
        print("NX2_PATCH_SKIP:", f"Could not find IsSwitch2Program block in {core}")
    core_text = core_text.replace(old_switch2, new_switch2, 1)

core.write_text(core_text)

# ---------------------------------------------------------------------------
# 2. Stop treating NPDM application-name bytes at +0x28 as a heap size.
#    For Switch 2, QueryPointerBufferSize must report 0x10000. Preserve the
#    existing legacy heuristic for Switch 1 titles to avoid unrelated changes.
# ---------------------------------------------------------------------------
nca = root / "src" / "core" / "loader" / "nca.cpp"
nca_text = nca.read_text()

if '#include "core/file_sys/program_metadata.h"' not in nca_text:
    anchor = '#include "core/file_sys/patch_manager.h"\n'
    if anchor not in nca_text:
        print("NX2_PATCH_SKIP:", f"Could not find ProgramMetadata include anchor in {nca}")
    nca_text = nca_text.replace(
        anchor,
        anchor + '#include "core/file_sys/program_metadata.h"\n',
        1,
    )

start_marker = "    // Read heap size from main.npdm in ExeFS\n"
end_marker = "    process.SetPointerBufferSize(CalculatePointerBufferSize(heap_size));"
if "Switch 2 NPDM: pointer buffer size fixed at 0x10000" not in nca_text:
    start = nca_text.find(start_marker)
    end = nca_text.find(end_marker, start)
    if start < 0 or end < 0:
        print("NX2_PATCH_SKIP:", f"Could not find pointer-buffer heuristic block in {nca}")
    end += len(end_marker)
    replacement = '''    // The old NX2 fork read +0x28 of main.npdm as a heap size. In the real
    // NPDM header that offset is inside application_name (the observed 0x6e6f69
    // was ASCII), so it cannot size the pointer buffer. Switch 2 uses 0x10000;
    // keep the historical heuristic only for non-Switch-2 titles.
    u64 heap_size = 0;
    u32 pointer_buffer_size = 0x8000;

    if (exefs) {
        const auto npdm_file = exefs->GetFile("main.npdm");
        if (npdm_file) {
            FileSys::ProgramMetadata metadata;
            const auto metadata_status = metadata.Load(npdm_file);
            if (metadata_status == ResultStatus::Success && metadata.IsSwitch2()) {
                pointer_buffer_size = 0x10000;
                LOG_INFO(Loader, "Switch 2 NPDM: pointer buffer size fixed at 0x10000");
            } else {
                const auto npdm_data = npdm_file->ReadAllBytes();
                if (npdm_data.size() >= 0x30) {
                    heap_size = *reinterpret_cast<const u64*>(&npdm_data[0x28]);
                    pointer_buffer_size = CalculatePointerBufferSize(heap_size);
                }
            }
        }
    }

    process.SetPointerBufferSize(pointer_buffer_size);'''
    nca_text = nca_text[:start] + replacement + nca_text[end:]

old_log = '''    LOG_INFO(Loader, "Set pointer buffer size to {:#x} bytes for ProgramID {:#018x} (Heap size: {:#x})",
             process.GetPointerBufferSize(), GetProgramId(), heap_size);'''
new_log = '''    LOG_INFO(Loader, "Set pointer buffer size to {:#x} bytes for ProgramID {:#018x}",
             process.GetPointerBufferSize(), GetProgramId());'''
if old_log in nca_text:
    nca_text = nca_text.replace(old_log, new_log, 1)

nca.write_text(nca_text)

# ---------------------------------------------------------------------------
# 3. Restore loose ExeFS discovery in Android game folders.
#    The pinned Eglantine scanner only recognizes file extensions, so selecting
#    an exefs folder containing main/main.npdm no longer creates a game entry.
# ---------------------------------------------------------------------------
helper = root / "src" / "android" / "app" / "src" / "main" / "java" / "org" / "yuzu" / "yuzu_emu" / "utils" / "GameHelper.kt"
helper_text = helper.read_text()

for old, new in [
    ("import android.net.Uri\n", "import android.net.Uri\nimport android.os.Build\nimport android.os.Environment\n"),
    ("import java.io.File\n", "import java.io.File\nimport java.nio.ByteBuffer\nimport java.nio.ByteOrder\n"),
    ("import org.yuzu.yuzu_emu.model.Game\n", "import org.yuzu.yuzu_emu.model.DirectoryType\nimport org.yuzu.yuzu_emu.model.Game\n"),
]:
    if new.strip() not in helper_text and old in helper_text:
        helper_text = helper_text.replace(old, new, 1)

if "addLibraryExeFSGames(games, gameDir)" not in helper_text:
    old = '''                addGamesRecursive(
                    games,
                    gamesByProgramId,
                    FileUtil.listFiles(gameDirUri),
                    scanDepth,
                    mountedContainerUris
                )
            } else {'''
    new = '''                addGamesRecursive(
                    games,
                    gamesByProgramId,
                    FileUtil.listFiles(gameDirUri),
                    scanDepth,
                    mountedContainerUris
                )
                addLibraryExeFSGames(games, gameDir)
            } else {'''
    if old not in helper_text:
        print("NX2_PATCH_SKIP:", f"Could not find game-folder scan insertion point in {helper}")
    helper_text = helper_text.replace(old, new, 1)

if "private fun addLibraryExeFSGames" not in helper_text:
    marker = "    fun restoreContentForGame(game: Game) {"
    block = '''    private fun addLibraryExeFSGames(games: MutableList<Game>, gameDir: GameDir) {
        findLibraryExeFSGames(gameDir).forEach { game ->
            val duplicate = games.any {
                it.path == game.path ||
                    (game.programId.isNotEmpty() && it.programId == game.programId)
            }
            if (duplicate) {
                return@forEach
            }
            games.add(game)
            if (preferences.getLong(game.keyAddedToLibraryTime, 0L) == 0L) {
                preferences.edit {
                    putLong(game.keyAddedToLibraryTime, System.currentTimeMillis())
                }
            }
        }
    }

'''
    if marker not in helper_text:
        print("NX2_PATCH_SKIP:", f"Could not find restoreContentForGame insertion point in {helper}")
    helper_text = helper_text.replace(marker, block + marker, 1)

helper_text = helper_text.replace(
    'private val externalContentExtensions = setOf("nsp", "xci", "nsz", "xcz")',
    'private val externalContentExtensions = setOf("nsp", "xci", "nsz", "xcz", "nup")',
)

if "private const val EXEFS_SEARCH_DEPTH" not in helper_text:
    marker = "    fun getGame(\n"
    block = '''    private const val EXEFS_SEARCH_DEPTH = 4
    private const val NPDM_MAX_SIZE = 1024 * 1024
    private const val NPDM_MAGIC = 0x4154454D // "META"
    private const val ACI0_MAGIC = 0x30494341 // "ACI0"
    private val exeFSSkippedDirs = setOf("romfs", "dlc")
    private val exeFSNonTitleDirs = setOf("exefs", "base", "updates", "update")
    private val exeFSStorageLevels = setOf("base", "update", "updates")

    // Loose ExeFS titles boot from exefs/main by real path. Accept either the
    // game folder containing exefs/ or the exefs folder itself.
    fun getExeFSGame(folder: File): Game? = findExeFSGames(folder).firstOrNull()

    fun findExeFSGames(folder: File, maxDepth: Int = EXEFS_SEARCH_DEPTH): List<Game> {
        val mains = mutableListOf<File>()

        File(folder, "main").takeIf { it.isFile }?.let { mains.add(it) }

        val exefsFolder = File(folder, "exefs")
        if (exefsFolder.isDirectory) {
            File(exefsFolder, "main").takeIf { it.isFile }?.let { mains.add(it) }
        }

        val baseExefsFolder = File(folder, "base/exefs")
        if (baseExefsFolder.isDirectory) {
            File(baseExefsFolder, "main").takeIf { it.isFile }?.let { mains.add(it) }
        }

        collectExeFSMains(folder, 0, maxDepth, mains)

        return mains.distinctBy { it.absolutePath }
            .sortedWith(
                compareByDescending<File> { exeFSVersion(it) }
                    .thenBy { it.parentFile?.relativeTo(folder)?.path ?: "" }
            )
            .map {
                Game(
                    title = exeFSTitle(it, folder),
                    path = it.absolutePath,
                    programId = exeFSProgramId(it)
                )
            }
    }

    fun hasAllFilesAccess(): Boolean =
        Build.VERSION.SDK_INT < Build.VERSION_CODES.R || Environment.isExternalStorageManager()

    fun findLibraryExeFSGames(gameDir: GameDir): List<Game> {
        if (gameDir.type != DirectoryType.GAME || !hasAllFilesAccess()) {
            return emptyList()
        }
        val path = PathUtil.getPathFromUri(gameDir.uriString.toUri()) ?: return emptyList()
        val folder = File(path)
        if (!folder.exists() || !folder.isDirectory) {
            return emptyList()
        }
        val maxDepth = if (gameDir.deepScan) EXEFS_SEARCH_DEPTH + 2 else EXEFS_SEARCH_DEPTH
        return findExeFSGames(folder, maxDepth).distinctBy {
            if (it.programId.isEmpty()) it.title else it.programId
        }
    }

    private fun collectExeFSMains(dir: File, depth: Int, maxDepth: Int, mains: MutableList<File>) {
        if (depth >= maxDepth) {
            return
        }
        val children = dir.listFiles() ?: return
        for (child in children) {
            if (!child.isDirectory || isSymlink(child)) {
                continue
            }
            val name = child.name.lowercase()
            if (name in exeFSSkippedDirs && depth > 0) {
                continue
            }
            if (name == "exefs") {
                File(child, "main").takeIf { it.isFile }?.let { mains.add(it) }
                collectExeFSMains(child, depth + 1, maxDepth, mains)
                continue
            }
            collectExeFSMains(child, depth + 1, maxDepth, mains)
        }
    }

    private fun isSymlink(file: File): Boolean {
        return try {
            val parent = file.parentFile?.canonicalFile ?: return false
            val resolved = File(parent, file.name)
            resolved.canonicalFile != resolved.absoluteFile
        } catch (_: Exception) {
            true
        }
    }

    private fun exeFSProgramId(main: File): String {
        val npdm = File(main.parentFile, "main.npdm")
        return try {
            if (!npdm.isFile || npdm.length() > NPDM_MAX_SIZE) {
                return ""
            }
            val buffer = ByteBuffer.wrap(npdm.readBytes()).order(ByteOrder.LITTLE_ENDIAN)
            if (buffer.limit() < 0x80 || buffer.getInt(0) != NPDM_MAGIC) {
                return ""
            }
            val aciOffset = buffer.getInt(0x70)
            if (aciOffset < 0 || aciOffset + 0x18 > buffer.limit() ||
                buffer.getInt(aciOffset) != ACI0_MAGIC
            ) {
                return ""
            }
            val programId = buffer.getLong(aciOffset + 0x10)
            if (programId == 0L) "" else java.lang.Long.toUnsignedString(programId)
        } catch (_: Exception) {
            ""
        }
    }

    private fun exeFSVersion(main: File): Long {
        var dir = main.parentFile ?: return -1
        if (dir.name.equals("exefs", true)) {
            dir = dir.parentFile ?: return -1
        }
        return dir.name.toLongOrNull() ?: -1
    }

    private fun exeFSTitle(main: File, folder: File): String {
        var dir = main.parentFile
        var isUpdate = false

        while (dir != null && dir != folder) {
            val name = dir.name
            val nameLower = name.lowercase()

            if (nameLower in setOf("update", "updates")) {
                isUpdate = true
            }

            val isStorageLevel = nameLower in exeFSStorageLevels ||
                name.all { it.isDigit() || it in 'a'..'f' || it in 'A'..'F' }

            if (!isStorageLevel && nameLower !in exeFSNonTitleDirs) {
                return if (isUpdate && !name.contains("update", ignoreCase = true)) {
                    "$name (update)"
                } else {
                    name
                }
            }

            dir = dir.parentFile
        }

        return folder.name
    }

'''
    if marker not in helper_text:
        print("NX2_PATCH_SKIP:", f"Could not find getGame insertion point in {helper}")
    helper_text = helper_text.replace(marker, block + marker, 1)

helper.write_text(helper_text)

game = root / "src" / "android" / "app" / "src" / "main" / "java" / "org" / "yuzu" / "yuzu_emu" / "model" / "Game.kt"
game_text = game.read_text()
game_text = game_text.replace(
    'listOf("xci", "nsp", "nca", "nro", "nsz", "xcz")',
    'listOf("xci", "nsp", "nca", "nro", "nsz", "xcz", "nup")',
)
game.write_text(game_text)

# ---------------------------------------------------------------------------
# 4. Add targeted diagnostics at the save-data handoff immediately preceding
#    the current DKB null-PC crash.
# ---------------------------------------------------------------------------
fsp = root / "src" / "core" / "hle" / "service" / "filesystem" / "fsp" / "fsp_srv.cpp"
fsp_text = fsp.read_text()

old = '''Result FSP_SRV::OpenSaveDataFileSystem(OutInterface<IFileSystem> out_interface,
                                       FileSys::SaveDataSpaceId space_id,
                                       FileSys::SaveDataAttribute attribute) {
    LOG_INFO(Service_FS, "called.");

    FileSys::VirtualDir dir{};
    R_TRY(save_data_controller->OpenSaveData(&dir, space_id, attribute));
'''
new = '''Result FSP_SRV::OpenSaveDataFileSystem(OutInterface<IFileSystem> out_interface,
                                       FileSys::SaveDataSpaceId space_id,
                                       FileSys::SaveDataAttribute attribute) {
    LOG_INFO(Service_FS, "called, space_id={}, {}", space_id, attribute.DebugInfo());

    FileSys::VirtualDir dir{};
    R_TRY(save_data_controller->OpenSaveData(&dir, space_id, attribute));
    LOG_INFO(Service_FS, "OpenSaveData returned directory={}", dir != nullptr);
'''
if "OpenSaveData returned directory=" not in fsp_text:
    if old not in fsp_text:
        print("NX2_PATCH_SKIP:", f"Could not find OpenSaveDataFileSystem diagnostic anchor in {fsp}")
    fsp_text = fsp_text.replace(old, new, 1)

old_return = '''    *out_interface =
        std::make_shared<IFileSystem>(system, std::move(dir), SizeGetter::FromStorageId(fsc, id));

    R_SUCCEED();
}'''
new_return = '''    *out_interface =
        std::make_shared<IFileSystem>(system, std::move(dir), SizeGetter::FromStorageId(fsc, id));
    LOG_INFO(Service_FS, "OpenSaveDataFileSystem returning IFileSystem for storage_id={}", id);

    R_SUCCEED();
}'''
if "returning IFileSystem for storage_id" not in fsp_text:
    if old_return not in fsp_text:
        print("NX2_PATCH_SKIP:", f"Could not find OpenSaveDataFileSystem return anchor in {fsp}")
    fsp_text = fsp_text.replace(old_return, new_return, 1)

fsp.write_text(fsp_text)

# ---------------------------------------------------------------------------
# 5. DKB Android NCE A/B: Build 57 still faults at SilverStar.nss+0x5AF1C8
#    after NCE skips hundreds of invalid accesses at low addresses. Use
#    Dynarmic for DKB only on Android so we can distinguish guest logic/service
#    gaps from NCE fault-recovery corruption. Other Switch 2 titles keep NCE.
# ---------------------------------------------------------------------------
deconstructed = root / "src" / "core" / "loader" / "deconstructed_rom_directory.cpp"
deconstructed_text = deconstructed.read_text()
nce_old = '''    Settings::SetNceEnabled(is_39bit);
'''
nce_new = '''#if defined(__ANDROID__)
    constexpr u64 DkbTitleId = 0x040074A01BF12000ULL;
    const bool force_dynarmic_for_dkb = metadata.GetTitleID() == DkbTitleId;
    if (force_dynarmic_for_dkb && is_39bit) {
        LOG_WARNING(Loader,
                    "NX2 Android DKB diagnostic: forcing Dynarmic instead of NCE after "
                    "Build 57 NCE invalid-access corruption at main+0x5AF1C8");
    }
    Settings::SetNceEnabled(is_39bit && !force_dynarmic_for_dkb);
#else
    Settings::SetNceEnabled(is_39bit);
#endif
'''
# Disabled 2026-11-05: the main+0x5AF1C8 fault was DKB's incomplete romfs
# (LocalizeHeapResource), not NCE. Forcing Dynarmic made DKB CPU-bound (~3 fps).
if False and "NX2 Android DKB diagnostic: forcing Dynarmic" not in deconstructed_text:
    if nce_old not in deconstructed_text:
        print("NX2_PATCH_SKIP:", f"Could not find NCE enable point in {deconstructed}")
    deconstructed_text = deconstructed_text.replace(nce_old, nce_new, 1)
deconstructed.write_text(deconstructed_text)

print("Applied Eglantine Switch 2 Android regression fixes:")
print(" - embedded NSP NPDM early detection / 12 GiB + 31.25 MHz startup")
print(" - Switch 2 QueryPointerBufferSize = 0x10000")
print(" - loose ExeFS Android library discovery restored")
print(" - NUP added to Android file/container extension sets")
print(" - save-data handoff diagnostics added")
print(" - (DKB keeps NCE; Dynarmic diagnostic disabled)")
