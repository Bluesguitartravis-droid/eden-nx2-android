#!/usr/bin/env python3
"""Configure zortos293/Lichii for this fork's Android releases.

Lichii's updater (#82) picks the newest build-N release that has an asset for
LICHII_BUILD_PLATFORM. It has no Android platform, so this adds android-arm64
mapped to this repo's Eden-NX2-Android-buildN-standard.apk release assets and
points the feed at Bluesguitartravis-droid/eden-nx2-android.
"""
from pathlib import Path
import sys

if len(sys.argv) != 3:
    raise SystemExit("usage: configure-lichii.py <upstream-dir> <build-tag>")
root = Path(sys.argv[1])
build_tag = sys.argv[2]
REPO = "Bluesguitartravis-droid/eden-nx2-android"

gradle = root / "src/android/app/build.gradle.kts"
g = gradle.read_text()
if 'applicationId = "dev.eden.eden_emulator"' not in g:
    raise SystemExit("Expected Eden package ID was not found")
anchor = '                        "-DENABLE_WEB_SERVICE=1", // Enable web service\n'
if anchor not in g:
    raise SystemExit("Could not find Android CMake argument block")
extra = (f'                        "-DLICHII_BUILD_TAG={build_tag}", // Eden NX2 release tag\n'
         '                        "-DLICHII_BUILD_PLATFORM=android-arm64",\n')
if "Eden NX2 release tag" not in g:
    g = g.replace(anchor, anchor + extra, 1)
if "-DENABLE_UPDATE_CHECKER=ON" not in g:
    g = g.replace(anchor, anchor + '                        "-DENABLE_UPDATE_CHECKER=ON",\n', 1)
gradle.write_text(g)

scm = root / "CMakeModules/GenerateSCMRev.cmake"
s = scm.read_text()
for old, new in [
    ('set(BUILD_AUTO_UPDATE_STABLE_REPO "zortos293/Lichii")', f'set(BUILD_AUTO_UPDATE_STABLE_REPO "{REPO}")'),
    ('set(BUILD_AUTO_UPDATE_API_PATH "/repos/zortos293/Lichii/releases?per_page=100")',
     f'set(BUILD_AUTO_UPDATE_API_PATH "/repos/{REPO}/releases?per_page=100")'),
    ('set(BUILD_AUTO_UPDATE_REPO "zortos293/Lichii")', f'set(BUILD_AUTO_UPDATE_REPO "{REPO}")'),
    ('set(REPO_NAME "Lichii")', 'set(REPO_NAME "Eden NX2 Android")'),
]:
    if old in s:
        s = s.replace(old, new, 1)
    elif new not in s:
        raise SystemExit(f"GenerateSCMRev.cmake shape changed: {old}")
scm.write_text(s)

net = root / "src/common/net/net.cpp"
n = net.read_text()
head = "std::vector<Asset> Release::GetPlatformAssets(std::string_view platform) const {\n"
android = head + '''    if (platform == "android-arm64") {
        // Eden NX2 Android fork: tag build-N ships Eden-NX2-Android-buildN-standard.apk.
        const std::string host_url = "https://github.com";
        const auto path =
            fmt::format("/{}/releases/download/{}/", Common::g_build_auto_update_repo, tag);
        const std::string_view number =
            std::string_view{tag}.starts_with("build-") ? std::string_view{tag}.substr(6)
                                                        : std::string_view{tag};
        const auto filename = fmt::format("Eden-NX2-Android-build{}-standard.apk", number);
        for (const auto& asset : assets) {
            if (asset == host_url + path + filename) {
                return {{.name = "Standard", .url = host_url, .path = path + filename,
                         .filename = filename}};
            }
        }
        return {};
    }
'''
if "Eden NX2 Android fork: tag build-N" not in n:
    if head not in n:
        raise SystemExit("Could not find GetPlatformAssets in net.cpp")
    n = n.replace(head, android, 1)
net.write_text(n)
print(f"Configured Lichii base for {build_tag} (android-arm64 feed: {REPO})")
