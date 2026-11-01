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

# Eden normally enables its Android update checker only for "nightly" builds.
# Enabling the nightly Gradle flavor would also change applicationId, preventing
# an in-place update of the build the Odin is already using. Enable only the
# CMake feature instead, preserving dev.eden.eden_emulator.
gradle = root / "src" / "android" / "app" / "build.gradle.kts"
gradle_text = gradle.read_text()
cmake_anchor = '                        "-DENABLE_WEB_SERVICE=1", // Enable web service\n'
cmake_update = cmake_anchor + '                        "-DENABLE_UPDATE_CHECKER=ON", // NX2 Android GitHub releases\n'
if "-DENABLE_UPDATE_CHECKER=ON\", // NX2 Android GitHub releases" not in gradle_text:
    if cmake_anchor not in gradle_text:
        raise SystemExit(f"Could not find Android CMake argument block in {gradle}")
    gradle.write_text(gradle_text.replace(cmake_anchor, cmake_update, 1))

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

net = root / "src" / "common" / "net" / "net.cpp"
net_text = net.read_text()
request_block = """        httplib::Request request{
            .method = "GET",
            .path = path,
        };
"""
request_with_headers = """        httplib::Request request{
            .method = "GET",
            .path = path,
        };
        // GitHub's REST API requires an identifying User-Agent. These headers are
        // harmless for Eden's other update endpoints and make our GitHub release
        // feed deterministic instead of depending on httplib defaults.
        request.headers.emplace("User-Agent", "Eden-NX2-Android");
        if (url.find("api.github.com") != std::string::npos) {
            request.headers.emplace("Accept", "application/vnd.github+json");
            request.headers.emplace("X-GitHub-Api-Version", "2022-11-28");
        }
"""
if request_with_headers not in net_text:
    if request_block not in net_text:
        raise SystemExit(f"Could not find HTTP request block in {net}")
    net.write_text(net_text.replace(request_block, request_with_headers))

print(f"Configured updater feed/version comparison for {build_tag}")
