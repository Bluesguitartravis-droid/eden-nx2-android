#!/usr/bin/env bash
# Developer check: run every patch step of android-build.yml against a local Lichii checkout,
# in workflow order, and report the ones that fail. Usage: scripts/dev-dry-run.sh <upstream-dir>
# The checkout is modified in place (use a throwaway worktree). The FFmpeg step is skipped.
set -u
up=$(cd "$1" && pwd)
cd "$(dirname "$0")/.."
fail=0
python3 scripts/configure-lichii.py "$up" build-dryrun >/dev/null 2>&1 || { echo "FAIL configure-lichii"; fail=1; }
while read -r script; do
  case "$script" in
    scripts/configure-lichii.py|scripts/apply-android-ffmpeg-av1.py) continue ;;
  esac
  if out=$(python3 "$script" "$up" 2>&1); then
    if echo "$out" | grep -q "NX2_PATCH_SKIP"; then
      echo "SKIP $script: $(echo "$out" | grep NX2_PATCH_SKIP | head -3 | tr '\n' ' ' | cut -c1-220)"
    fi
  else
    echo "FAIL $script: $(echo "$out" | tail -2 | tr '\n' ' ' | cut -c1-240)"
    fail=1
  fi
done < <(grep -oE "python3 scripts/[A-Za-z0-9_.-]+\.py" .github/workflows/android-build.yml | awk '{print $2}')
[ "$fail" = 0 ] && echo "all patch steps applied"
exit $fail
