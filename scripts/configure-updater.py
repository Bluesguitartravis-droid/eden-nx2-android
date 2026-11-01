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
print(f"Configured updater feed for {build_tag}: {path}")
