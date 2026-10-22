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
  (cd vendor/gryphon && npm ci --include=dev)
  # Skip `build:all` (which does `git submodule update` — only valid in a
  # git repo with vendor/gryphon registered as a submodule, i.e. the
  # standalone polleoai/athena release repo). Locally we trust the
  # working-tree pin; build directly. CI handles submodule init via
  # actions/checkout@v4 with `submodules: recursive` before calling build:all.
  npm run build:gryphon
  npm run build
  verify_artifacts "npm"
}

# Obsidian's review rebuilds the release from GitHub's source archive (no .git,
# so vendor/gryphon arrives empty and ensure-gryphon fetches it by pin) with
# NODE_ENV=production, then compares main.js byte for byte with the released
# asset. Do the same with HEAD and compare against the npm-path build. Before
# this check, a production-mode install bundled 1,739 bytes less and the review
# reported "Build output does not match" on 1.8.1, 1.8.14 and 1.8.17.
run_review_path() {
  local want="$1" dir got
  echo "=== review build: source archive of HEAD + NODE_ENV=production ==="
  dir="$(mktemp -d -t athena-review-XXXXXX)"
  git archive HEAD | tar -x -C "$dir"
  (cd "$dir" && npm ci --no-audit --no-fund && NODE_ENV=production npm run build)
  got="$(shasum -a 256 "$dir/main.js" | cut -d' ' -f1)"
  rm -rf "$dir"
  if [ "$got" != "$want" ]; then
    echo "[review] FAIL: main.js from the source archive ($got) differs from the npm build ($want)"
    echo "         Obsidian's review will report \"Build output does not match the released main.js artifact\"."
    exit 1
  fi
  echo "[review] OK: source-archive production build reproduces main.js byte for byte"
}

run_pnpm_path() {
  if ! command -v pnpm >/dev/null 2>&1; then
    echo "=== pnpm install + build:gryphon + build === (skipped — pnpm not installed locally)"
    return 0
  fi
  echo "=== pnpm install + build:gryphon + build ==="
  rm -rf node_modules vendor/gryphon/node_modules .obsidian/plugins/athena/main.js
  pnpm install --frozen-lockfile=false
  (cd vendor/gryphon && pnpm install --frozen-lockfile=false --production=false)
  # See npm-path comment for why we skip build:all locally.
  npm run build:gryphon
  npm run build
  verify_artifacts "pnpm"
}

# versions.json must declare THIS release before the tag is pushed — the
# release workflow re-checks it, but by then the tag is already public.
# Record with:  node scripts/versions-json.js --record
check_versions_json() {
  local tag
  tag="$(node -p "require('./manifest.json').version")"
  echo "=== versions.json gate for $tag ==="
  local known=()
  if command -v gh >/dev/null 2>&1 && gh release list -R polleoai/athena --limit 1000 --json tagName --jq '.[].tagName' > "${TMPDIR:-/tmp}/athena-public-releases.txt" 2>/dev/null; then
    known=(--known-releases "${TMPDIR:-/tmp}/athena-public-releases.txt")
  else
    echo "  (gh unavailable — skipping the every-entry-is-a-public-release check; CI still runs it)"
  fi
  node scripts/versions-json.js --check --release "$tag" ${known[@]+"${known[@]}"}
}

check_versions_json

# Same gate as release.yml's "Assert vendor/gryphon.pin matches the submodule":
# a curated tree can carry the new submodule pointer with a stale pin file
# (public 1.8.17's first tag did, and failed in CI).
echo "=== vendor/gryphon.pin gate ==="
node scripts/sync-gryphon-pin.js --check

if [ "${1:-}" = "--fresh" ]; then
  # Clone HEAD into a tmp dir and run the smoke test there, exactly mirroring
  # what Obsidian's sandbox sees. The current working tree is untouched.
  TMP_DIR="$(mktemp -d -t athena-smoke-XXXXXX)"
  trap 'rm -rf "$TMP_DIR"' EXIT
  echo "Cloning $REPO_ROOT into $TMP_DIR"
  git clone --recurse-submodules "$REPO_ROOT" "$TMP_DIR/athena"
  cd "$TMP_DIR/athena"
  run_npm_path
  run_review_path "$(shasum -a 256 main.js | cut -d' ' -f1)"
  run_pnpm_path
  echo "Fresh-clone smoke test PASSED"
else
  run_npm_path
  # The archive is of HEAD: commit first, or this compares against older source.
  run_review_path "$(shasum -a 256 main.js | cut -d' ' -f1)"
  run_pnpm_path
  echo "Working-tree smoke test PASSED"
fi
