#!/usr/bin/env bash
set -euo pipefail

UPSTREAM_DIR="${1:-upstream}"
EGLANTINE_REMOTE="${EGLANTINE_REMOTE:-https://github.com/zortos293/Eglantine.git}"

# Intentionally small, platform-neutral fixes selected from Eglantine NX2.
# Keep this list conservative so Android/Switch regressions are easy to bisect.
COMMITS=(
  a4a746b47b1d1ee02ddf1fe528c7db05800eec2c  # Ampere RET/R0 text fix
  d2ac2275247a9ecb3e1404606f7026c6402533b8  # Switch 2 docked/handheld refresh
  ca524c9aa883abc4d1c5aeb8aa4d2f9eb0b4225c  # Vulkan image-view dimension guard
  ea33ad1865970ee731e5f74dbcc26ca472798afe  # atomic Vulkan pipeline cache
  ddd367a8152f1c37bfc972475b40afece2da31ac  # device page-run continuity
  59047f0e6fd149e8187092ec0d9f419aa03e875f  # shader object-pool destructors
)

if ! git -C "$UPSTREAM_DIR" remote get-url eglantine >/dev/null 2>&1; then
  git -C "$UPSTREAM_DIR" remote add eglantine "$EGLANTINE_REMOTE"
fi

for sha in "${COMMITS[@]}"; do
  echo
  echo "==> Eglantine fix $sha"
  git -C "$UPSTREAM_DIR" fetch --quiet --no-tags eglantine "$sha"
  parent="$(git -C "$UPSTREAM_DIR" rev-parse "$sha^")"
  patch_file="$(mktemp)"
  git -C "$UPSTREAM_DIR" diff --binary "$parent" "$sha" -- > "$patch_file"

  if git -C "$UPSTREAM_DIR" apply --reverse --check "$patch_file" >/dev/null 2>&1; then
    echo "Already present; skipping."
  else
    git -C "$UPSTREAM_DIR" apply --check "$patch_file"
    git -C "$UPSTREAM_DIR" apply "$patch_file"
    echo "Applied."
  fi
  rm -f "$patch_file"
done

echo
echo "Selected Eglantine fixes applied:"
git -C "$UPSTREAM_DIR" diff --stat
