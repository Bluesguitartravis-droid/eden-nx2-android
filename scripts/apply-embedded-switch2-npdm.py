#!/usr/bin/env python3
from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: apply-embedded-switch2-npdm.py <upstream-dir>")

root = Path(sys.argv[1])
core = root / "src" / "core" / "core.cpp"
text = core.read_text()

include_anchor = '#include "core/file_sys/fs_filesystem.h"\n'
include_block = (
    '#include "core/file_sys/content_archive.h"\n'
    '#include "core/file_sys/fs_filesystem.h"\n'
)
if '#include "core/file_sys/content_archive.h"' not in text:
    if include_anchor not in text:
        raise SystemExit(f"Could not find core include anchor in {core}")
    text = text.replace(include_anchor, include_block, 1)

submission_anchor = '#include "core/file_sys/savedata_factory.h"\n'
submission_block = (
    '#include "core/file_sys/savedata_factory.h"\n'
    '#include "core/file_sys/submission_package.h"\n'
)
if '#include "core/file_sys/submission_package.h"' not in text:
    if submission_anchor not in text:
        raise SystemExit(f"Could not find submission-package include anchor in {core}")
    text = text.replace(submission_anchor, submission_block, 1)

old = '''// Switch 2 titles boot as a loose ExeFS, with main.npdm next to main.
bool IsSwitch2Program(const FileSys::VirtualFile& file) {
    const auto dir = file != nullptr ? file->GetContainingDirectory() : nullptr;
    const auto npdm = dir != nullptr ? dir->GetFile("main.npdm") : nullptr;
    FileSys::ProgramMetadata metadata;
    return npdm != nullptr && metadata.Load(npdm) == Loader::ResultStatus::Success &&
           metadata.IsSwitch2();
}
'''

new = '''bool IsSwitch2Npdm(const FileSys::VirtualFile& npdm) {
    if (npdm == nullptr) {
        return false;
    }

    FileSys::ProgramMetadata metadata;
    return metadata.Load(npdm) == Loader::ResultStatus::Success && metadata.IsSwitch2();
}

// Switch 2 loose dumps have main.npdm beside main. Our generated plaintext NSPs instead carry
// that same NPDM inside the Program NCA's ExeFS. Probe both forms before system initialization so
// memory layout, guest counter frequency and the rest of the Switch 2 path are selected correctly.
bool IsSwitch2Program(const FileSys::VirtualFile& file) {
    if (file == nullptr) {
        return false;
    }

    const auto dir = file->GetContainingDirectory();
    if (dir != nullptr && IsSwitch2Npdm(dir->GetFile("main.npdm"))) {
        return true;
    }

    const auto& name = file->GetName();
    if (!name.ends_with(".nsp")) {
        return false;
    }

    FileSys::NSP nsp(file);
    if (nsp.GetStatus() != Loader::ResultStatus::Success || nsp.IsExtractedType()) {
        return false;
    }

    const u64 program_id = nsp.GetProgramTitleID();
    if (program_id == 0) {
        return false;
    }

    const auto program_nca =
        nsp.GetNCA(program_id, FileSys::ContentRecordType::Program);
    if (program_nca == nullptr ||
        program_nca->GetStatus() != Loader::ResultStatus::Success) {
        return false;
    }

    const auto exefs = program_nca->GetExeFS();
    if (exefs == nullptr) {
        return false;
    }

    if (IsSwitch2Npdm(exefs->GetFile("main.npdm"))) {
        LOG_INFO(Core,
                 "Switch 2 program detected from embedded NSP Program NCA NPDM ({})",
                 name);
        return true;
    }

    return false;
}
'''

if 'Switch 2 program detected from embedded NSP Program NCA NPDM' not in text:
    if old not in text:
        raise SystemExit(f"Could not find IsSwitch2Program block in {core}")
    text = text.replace(old, new, 1)

core.write_text(text)
print(f"Patched embedded Switch 2 NPDM detection: {core}")
