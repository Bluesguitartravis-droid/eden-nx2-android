#!/usr/bin/env python3
"""Build 64: restore direct Android ExeFS launch and mount base+update RomFS."""
from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: apply-nx2-direct-exefs.py <upstream-dir>")
root = Path(sys.argv[1]).resolve()
games = root / "src/android/app/src/main/java/io/github/zortos293/lichii/ui/GamesFragment.kt"
data = games.read_text()
for old, new in [
    ("import android.content.Intent\n", "import android.content.Intent\nimport android.net.Uri\nimport android.os.Build\nimport android.os.Environment\nimport android.provider.Settings\n"),
    ("import androidx.core.view.ViewCompat\n", "import androidx.core.net.toUri\nimport androidx.core.view.ViewCompat\n"),
    ("import io.github.zortos293.lichii.model.HomeViewModel\n", "import io.github.zortos293.lichii.model.HomeViewModel\nimport io.github.zortos293.lichii.utils.GameHelper\nimport io.github.zortos293.lichii.utils.PathUtil\n"),
    ("import java.util.Locale\n", "import java.io.File\nimport java.util.Locale\nimport com.google.android.material.dialog.MaterialAlertDialogBuilder\n"),
]:
    if new.strip() not in data:
        if old not in data:
            raise SystemExit(f"Cannot insert GamesFragment import: {old.strip()}")
        data = data.replace(old, new, 1)

if "private fun launchExeFSFolder(uri: Uri)" not in data:
    marker = "    private fun getCurrentViewType(): Int {\n"
    new_picker = """    // The library scanner and direct ExeFS launch are different actions.
    // Direct launch uses real paths so the native loader can read sibling files.
    private val getExeFSFolder =
        registerForActivityResult(ActivityResultContracts.OpenDocumentTree()) { result ->
            if (result != null) {
                launchExeFSFolder(result)
            }
        }

    private fun showAddDirectoryMenu(anchor: View) {
        val popup = PopupMenu(requireContext(), anchor)
        popup.menu.add(0, 0, 0, R.string.add_games)
        popup.menu.add(0, 1, 1, R.string.launch_exefs_folder)
        popup.setOnMenuItemClickListener { item ->
            when (item.itemId) {
                0 -> getGamesDirectory.launch(null)
                1 -> getExeFSFolder.launch(null)
            }
            true
        }
        popup.show()
    }

    private fun launchExeFSFolder(uri: Uri) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R &&
            !Environment.isExternalStorageManager()
        ) {
            Toast.makeText(requireContext(), R.string.exefs_folder_needs_permission,
                           Toast.LENGTH_LONG).show()
            val intent = Intent(Settings.ACTION_MANAGE_APP_ALL_FILES_ACCESS_PERMISSION)
            intent.data = ("package:" + requireContext().packageName).toUri()
            startActivity(intent)
            return
        }
        val path = PathUtil.getPathFromUri(uri)
        if (path == null) {
            Toast.makeText(requireContext(), R.string.exefs_folder_bad_path,
                           Toast.LENGTH_LONG).show()
            return
        }
        val folder = File(path)
        val available = GameHelper.findExeFSGames(folder)
        when (available.size) {
            0 -> Toast.makeText(requireContext(), R.string.exefs_folder_no_main,
                                Toast.LENGTH_LONG).show()
            1 -> launchExeFSGame(available.first())
            else -> {
                val choices = available.map { game ->
                    val parent = File(game.path).parentFile
                    parent?.relativeToOrNull(folder)?.path?.takeIf { it.isNotEmpty() }
                        ?: parent?.name.orEmpty()
                }.toTypedArray()
                MaterialAlertDialogBuilder(requireContext())
                    .setTitle(R.string.exefs_folder_choose)
                    .setItems(choices) { _, index -> launchExeFSGame(available[index]) }
                    .setNegativeButton(android.R.string.cancel, null)
                    .show()
            }
        }
    }

    private fun launchExeFSGame(game: Game) {
        findNavController().navigate(HomeNavigationDirections.actionGlobalEmulationActivity(game))
    }

"""
    if marker not in data:
        raise SystemExit("Cannot find GamesFragment insertion marker")
    data = data.replace(marker, new_picker + marker, 1)

old_click = """        binding.addDirectory.setOnClickListener {
            getGamesDirectory.launch(Intent(Intent.ACTION_OPEN_DOCUMENT_TREE).data)
        }"""
new_click = """        binding.addDirectory.setOnClickListener {
            showAddDirectoryMenu(binding.addDirectory)
        }"""
if new_click not in data:
    if old_click not in data:
        raise SystemExit("Cannot find folder button handler")
    data = data.replace(old_click, new_click, 1)
games.write_text(data)

strings = root / "src/android/app/src/main/res/values/strings.xml"
data = strings.read_text()
if '<string name="launch_exefs_folder">' not in data:
    marker = "\n</resources>"
    extra = """
    <!-- Direct loose ExeFS launch on Android -->
    <string name="launch_exefs_folder">Launch ExeFS game folder</string>
    <string name="exefs_folder_no_main">No exefs/main found here. Choose the parent game folder or exefs itself.</string>
    <string name="exefs_folder_choose">Choose which ExeFS to launch</string>
    <string name="exefs_folder_bad_path">The chosen folder has no usable filesystem path. Choose internal or SD card storage.</string>
    <string name="exefs_folder_needs_permission">Allow All files access for Eden NX2, then choose the folder again.</string>
"""
    if marker not in data:
        raise SystemExit("Cannot find strings resource end")
    data = data.replace(marker, extra + marker, 1)
strings.write_text(data)

native = root / "src/core/loader/deconstructed_rom_directory.cpp"
data = native.read_text()
if '#include "core/file_sys/nup_container.h"' not in data:
    anchor = '#include "core/file_sys/patch_manager.h"\n'
    if anchor not in data:
        raise SystemExit("Cannot find native include anchor")
    data = data.replace(anchor, anchor + '#include "core/file_sys/nup_container.h"\n'
                        + '#include "core/file_sys/program_metadata.h"\n', 1)
if "#include <string>\n" not in data:
    data = data.replace("#include <string_view>\n", "#include <string>\n#include <string_view>\n", 1)
start_marker = "FileSys::VirtualFile FindLooseRomFS(const FileSys::VirtualDir& exefs_dir) {\n"
start = data.find(start_marker)
# Stop before the first helper that follows FindLooseRomFS: newer upstreams put
# NewestProductTag there, ahead of the display-version comment.
end = -1
for end_marker in ("// The newest three-digit tag in a directory's file names",
                   "// The display version of a dump without control data"):
    end = data.find(end_marker, start)
    if end >= 0:
        break
if "NX2_LOOSE_ROMFS" not in data:
    if start < 0 or end < 0 or 'LOG_INFO(Loader, "Loose RomFS image ready: elapsed_ms={}"' not in data[start:end]:
        raise SystemExit("Unexpected native loose RomFS loader shape")
    replacement = """FileSys::VirtualFile FindLooseRomFS(const FileSys::VirtualDir& exefs_dir) {
    if (exefs_dir == nullptr) {
        return nullptr;
    }
    for (const auto& f : exefs_dir->GetFiles()) {
        if (f->GetExtension() == "romfs") {
            LOG_INFO(Loader, "NX2_LOOSE_ROMFS mode=sidecar-image file={}", f->GetFullPath());
            return f;
        }
    }
    const auto parent = exefs_dir->GetParentDirectory();
    if (parent == nullptr) {
        return nullptr;
    }
    const auto romfs_dir = parent->GetSubdirectory("romfs");
    if (romfs_dir == nullptr) {
        LOG_WARNING(Loader, "NX2_LOOSE_ROMFS missing sibling romfs/ for {}",
                    exefs_dir->GetFullPath());
        return nullptr;
    }

    FileSys::VirtualDir base_romfs{};
    const auto updates_dir = parent->GetParentDirectory();
    if (updates_dir != nullptr &&
        (updates_dir->GetName() == "updates" || updates_dir->GetName() == "update")) {
        const auto game_dir = updates_dir->GetParentDirectory();
        const auto base = game_dir != nullptr ? game_dir->GetSubdirectory("base") : nullptr;
        const auto base_exefs = base != nullptr ? base->GetSubdirectory("exefs") : nullptr;
        const auto source_npdm = exefs_dir->GetFile("main.npdm");
        const auto base_npdm = base_exefs != nullptr
                                   ? base_exefs->GetFile("main.npdm") : nullptr;
        if (source_npdm != nullptr && base_npdm != nullptr) {
            FileSys::ProgramMetadata update_metadata{};
            FileSys::ProgramMetadata base_metadata{};
            if (update_metadata.Load(source_npdm) == ResultStatus::Success &&
                base_metadata.Load(base_npdm) == ResultStatus::Success &&
                update_metadata.GetTitleID() == base_metadata.GetTitleID()) {
                base_romfs = base->GetSubdirectory("romfs");
            }
        }
        if (base_romfs == nullptr) {
            LOG_WARNING(Loader,
                        "NX2_LOOSE_ROMFS update has no matching base/romfs for {}; "
                        "using update-only tree",
                        exefs_dir->GetFullPath());
        }
    }

    // Indexing an extracted RomFS is expensive; cache the source pair.
    static std::mutex cache_mutex;
    static std::map<std::string, std::weak_ptr<FileSys::VfsFile>> cache;
    std::string cache_key = romfs_dir->GetFullPath();
    if (base_romfs != nullptr) {
        cache_key += "|base=" + base_romfs->GetFullPath();
    }
    std::scoped_lock lk{cache_mutex};
    auto& cached = cache[cache_key];
    if (auto built = cached.lock()) {
        return built;
    }

    FileSys::VirtualDir effective = romfs_dir;
    if (base_romfs != nullptr) {
        effective = FileSys::NupContainer::MergeShadowDirs(base_romfs, romfs_dir);
        LOG_INFO(Loader, "NX2_LOOSE_ROMFS mode=base+update update={} base={}",
                 romfs_dir->GetFullPath(), base_romfs->GetFullPath());
    } else {
        LOG_INFO(Loader, "NX2_LOOSE_ROMFS mode=single romfs={}", romfs_dir->GetFullPath());
    }
    const auto started = std::chrono::steady_clock::now();
    auto built = FileSys::CreateRomFS(effective);
    LOG_INFO(Loader, "NX2_LOOSE_ROMFS image ready: size={:#x} elapsed_ms={}",
             built != nullptr ? built->GetSize() : 0,
             std::chrono::duration_cast<std::chrono::milliseconds>(
                 std::chrono::steady_clock::now() - started).count());
    cached = built;
    return built;
}

"""
    data = data[:start] + replacement + data[end:]
native.write_text(data)
print("Direct ExeFS Android launch, version chooser and native base+update RomFS are enabled")
