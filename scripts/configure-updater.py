#!/usr/bin/env python3
from pathlib import Path
import sys

if len(sys.argv) != 3:
    raise SystemExit("usage: configure-updater.py <upstream-dir> <build-tag>")

root = Path(sys.argv[1])
build_tag = sys.argv[2]
path = root / "CMakeModules" / "GenerateSCMRev.cmake"
text = path.read_text()

marker = "set(BUILD_ID ${GIT_REFSPEC})"
if marker not in text:
    raise SystemExit(f"Could not find updater insertion point in {path}")

block = f'''# NX2 Android fork update feed. GitHub Releases provide a stable APK URL and metadata.
# Android will still require the user to approve the package-install confirmation.
if(DEFINED ENV{{NX2_ANDROID_BUILD_TAG}} AND NOT "$ENV{{NX2_ANDROID_BUILD_TAG}}" STREQUAL "")
    set(BUILD_VERSION "$ENV{{NX2_ANDROID_BUILD_TAG}}")
    set(GIT_DESC "${{BUILD_VERSION}}")
    set(BUILD_AUTO_UPDATE_WEBSITE "https://github.com")
    set(BUILD_AUTO_UPDATE_API "api.github.com")
    set(BUILD_AUTO_UPDATE_API_PATH "/repos/Bluesguitartravis-droid/eden-nx2-android/releases/latest")
    set(BUILD_AUTO_UPDATE_REPO "Bluesguitartravis-droid/eden-nx2-android")
    set(REPO_NAME "Eden NX2 Android")
endif()

'''
if "NX2 Android fork update feed" not in text:
    text = text.replace(marker, block + marker)

path.write_text(text)

checker = root / "src" / "frontend_common" / "update_checker.cpp"
checker_text = checker.read_text()
if "#include <charconv>" not in checker_text:
    checker_text = checker_text.replace("#include <fmt/format.h>", "#include <charconv>\n#include <fmt/format.h>")

old_compare = """    if (tag != build)
        return latest;

    return std::nullopt;
"""
new_compare = """    // NX2 Android releases use monotonically increasing build-N tags. Do not
    // offer a downgrade when a locally installed/test build is newer than the
    // latest published release.
    const auto parse_build_number = [](std::string_view value) -> std::optional<u64> {
        constexpr std::string_view prefix{"build-"};
        if (!value.starts_with(prefix)) {
            return std::nullopt;
        }
        u64 number{};
        const auto digits = value.substr(prefix.size());
        const auto [ptr, ec] =
            std::from_chars(digits.data(), digits.data() + digits.size(), number);
        if (ec != std::errc{} || ptr != digits.data() + digits.size()) {
            return std::nullopt;
        }
        return number;
    };

    const auto latest_build = parse_build_number(tag);
    const auto current_build = parse_build_number(build);
    if (latest_build && current_build) {
        return *latest_build > *current_build ? latest : std::nullopt;
    }

    if (tag != build)
        return latest;

    return std::nullopt;
"""
if old_compare not in checker_text:
    raise SystemExit(f"Could not find update comparison block in {checker}")
checker.write_text(checker_text.replace(old_compare, new_compare))
print(f"Configured updater feed/version comparison for {build_tag}")
