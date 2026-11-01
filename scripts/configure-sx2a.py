#!/usr/bin/env python3
from pathlib import Path
import sys

if len(sys.argv) != 3:
    raise SystemExit("usage: configure-sx2a.py <upstream-dir> <build-tag>")

root = Path(sys.argv[1])
build_tag = sys.argv[2]

# Keep the same package ID as our known-good build #15 so this installs as an
# in-place update and preserves the user's existing Eden data/settings.
gradle = root / "src" / "android" / "app" / "build.gradle.kts"
gradle_text = gradle.read_text()
gradle_text = gradle_text.replace(
    '        applicationId = "dev.eden.nx2"\n',
    '        applicationId = "dev.eden.eden_emulator"\n',
    1,
)

# Enable Eden's existing Android updater without switching to the Nightly
# flavor/applicationId.
cmake_anchor = '                        "-DENABLE_WEB_SERVICE=1", // Enable web service\n'
cmake_update = cmake_anchor + '                        "-DENABLE_UPDATE_CHECKER=ON", // NX2 Android GitHub releases\n'
if 'NX2 Android GitHub releases' not in gradle_text:
    if cmake_anchor not in gradle_text:
        raise SystemExit(f"Could not find Android CMake argument block in {gradle}")
    gradle_text = gradle_text.replace(cmake_anchor, cmake_update, 1)
gradle.write_text(gradle_text)

# Point the existing Eden updater at this repository's GitHub Releases and bake
# the workflow build number into the version string.
scm = root / "CMakeModules" / "GenerateSCMRev.cmake"
scm_text = scm.read_text()
marker = "set(BUILD_ID ${GIT_REFSPEC})"
if marker not in scm_text:
    raise SystemExit(f"Could not find updater insertion point in {scm}")
block = f'''# NX2 Android fork update feed.
set(BUILD_VERSION "{build_tag}")
set(GIT_DESC "${{BUILD_VERSION}}")
set(BUILD_AUTO_UPDATE_WEBSITE "https://github.com")
set(BUILD_AUTO_UPDATE_API "api.github.com")
set(BUILD_AUTO_UPDATE_API_PATH "/repos/Bluesguitartravis-droid/eden-nx2-android/releases/latest")
set(BUILD_AUTO_UPDATE_REPO "Bluesguitartravis-droid/eden-nx2-android")
set(REPO_NAME "Eden NX2 Android")

'''
if "NX2 Android fork update feed" not in scm_text:
    scm_text = scm_text.replace(marker, block + marker)
scm.write_text(scm_text)

# Avoid downgrade prompts: build-24 should not offer build-23.
checker = root / "src" / "frontend_common" / "update_checker.cpp"
checker_text = checker.read_text()
if "#include <charconv>" not in checker_text:
    checker_text = checker_text.replace(
        "#include <fmt/format.h>",
        "#include <charconv>\n#include <fmt/format.h>",
    )

old_compare = """    if (tag != build)
        return latest;

    return std::nullopt;
"""
new_compare = """    const auto parse_build_number = [](std::string_view value) -> std::optional<u64> {
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
if "parse_build_number" not in checker_text:
    if old_compare not in checker_text:
        raise SystemExit(f"Could not find update comparison block in {checker}")
    checker_text = checker_text.replace(old_compare, new_compare)
checker.write_text(checker_text)

# GitHub's REST API requires an identifying User-Agent.
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
        request.headers.emplace("User-Agent", "Eden-NX2-Android");
        if (url.find("api.github.com") != std::string::npos) {
            request.headers.emplace("Accept", "application/vnd.github+json");
            request.headers.emplace("X-GitHub-Api-Version", "2022-11-28");
        }
"""
if 'User-Agent", "Eden-NX2-Android"' not in net_text:
    if request_block not in net_text:
        raise SystemExit(f"Could not find HTTP request block in {net}")
    net_text = net_text.replace(request_block, request_with_headers)
net.write_text(net_text)

print(f"Configured SX2A/PokeDinner base for {build_tag}")
print("Package ID: dev.eden.eden_emulator")
