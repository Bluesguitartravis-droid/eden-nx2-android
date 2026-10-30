#!/bin/sh
set -eu

REPO_URL="${NX2_REPO_URL:-https://git.eden-emu.dev/Takoroka/eden-nx2.git}"
BRANCH="${NX2_BRANCH:-eden-nx2}"

if [ ! -d upstream/.git ]; then
  git clone --recursive --branch "$BRANCH" --single-branch "$REPO_URL" upstream
else
  git -C upstream fetch origin "$BRANCH"
  git -C upstream checkout "$BRANCH"
  git -C upstream pull --ff-only origin "$BRANCH"
  git -C upstream submodule update --init --recursive
fi

for patch in patches/*.patch; do
  [ -f "$patch" ] || continue
  git -C upstream apply --check "../$patch"
  git -C upstream apply "../$patch"
done

cd upstream/src/android
chmod +x gradlew
./gradlew assembleStandardRelease --stacktrace --no-daemon
