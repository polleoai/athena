#!/usr/bin/env bash
# release-smoke-test.sh — verify the Athena plugin builds end-to-end from a
# clean checkout. Runs before tagging a release.
#
# Why: Obsidian's Community Plugins scorecard runs build verification in a
# sandboxed environment that uses pnpm (not npm). If our build silently
# depends on npm-only behavior, the scorecard will fail even though the
# release artifacts work in our hands. This script runs both `npm` and
# (when available) `pnpm` install paths against a fresh clone, building the
# full bundle each time, and asserts that the three required artifacts are
# present with non-zero size.
#
# Usage:
#   scripts/release-smoke-test.sh           # smoke-test current working tree
#   scripts/release-smoke-test.sh --fresh   # clone HEAD into /tmp and test
#
# The release workflow (.github/workflows/release.yml) is the actual gate
# for shipped releases; this script is the local-dev gate the maintainer
# runs before pushing the tag.

set -euo pipefail

cd "$(dirname "$0")/.."
REPO_ROOT="$(pwd)"

REQUIRED_ARTIFACTS=(
  ".obsidian/plugins/athena/main.js"
  ".obsidian/plugins/athena/manifest.json"
  ".obsidian/plugins/athena/styles.css"
)

verify_artifacts() {
  local label="$1"
  for artifact in "${REQUIRED_ARTIFACTS[@]}"; do
    if [ ! -s "$artifact" ]; then
      echo "[$label] FAIL: $artifact missing or empty after build"
      exit 1
    fi
  done
  echo "[$label] OK: all required artifacts present and non-empty"
}

run_npm_path() {
  echo "=== npm install + build:gryphon + build ==="
  rm -rf node_modules vendor/gryphon/node_modules .obsidian/plugins/athena/main.js
  npm ci
  (cd vendor/gryphon && npm ci)
  # Skip `build:all` (which does `git submodule update` — only valid in a
  # git repo with vendor/gryphon registered as a submodule, i.e. the
  # standalone polleoai/athena release repo). Locally we trust the
  # working-tree pin; build directly. CI handles submodule init via
  # actions/checkout@v4 with `submodules: recursive` before calling build:all.
  npm run build:gryphon
  npm run build
  verify_artifacts "npm"
}

run_pnpm_path() {
  if ! command -v pnpm >/dev/null 2>&1; then
    echo "=== pnpm install + build:gryphon + build === (skipped — pnpm not installed locally)"
    return 0
  fi
  echo "=== pnpm install + build:gryphon + build ==="
  rm -rf node_modules vendor/gryphon/node_modules .obsidian/plugins/athena/main.js
  pnpm install --frozen-lockfile=false
  (cd vendor/gryphon && pnpm install --frozen-lockfile=false)
  # See npm-path comment for why we skip build:all locally.
  npm run build:gryphon
  npm run build
  verify_artifacts "pnpm"
}

if [ "${1:-}" = "--fresh" ]; then
  # Clone HEAD into a tmp dir and run the smoke test there, exactly mirroring
  # what Obsidian's sandbox sees. The current working tree is untouched.
  TMP_DIR="$(mktemp -d -t athena-smoke-XXXXXX)"
  trap 'rm -rf "$TMP_DIR"' EXIT
  echo "Cloning $REPO_ROOT into $TMP_DIR"
  git clone --recurse-submodules "$REPO_ROOT" "$TMP_DIR/athena"
  cd "$TMP_DIR/athena"
  run_npm_path
  run_pnpm_path
  echo "Fresh-clone smoke test PASSED"
else
  run_npm_path
  run_pnpm_path
  echo "Working-tree smoke test PASSED"
fi
