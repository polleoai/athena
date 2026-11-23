#!/usr/bin/env bash
# sync.sh — the hourly maintenance run (com.athena.sync -> `npm run sync`).
#
# Three INDEPENDENT goals. They used to be one `&&` chain:
#
#     (pull) && npm run build:all && npm run kb:auto-ingest
#
# which meant any earlier link failing silently cancelled the later ones. Both
# halves of that bit us for real:
#   * build:all's `git submodule update ... vendor/gryphon` failed on a relative
#     pathspec for 61 consecutive runs (fixed in 14f8dc6), so kb:auto-ingest
#     never ran.
#   * then on 2026-10-09 23:55 the pull aborted on an uncommitted local change
#     to package.json, so build:all never ran and the installed Gryphon plugin
#     stayed a release behind — even though the pathspec fix was already in.
#
# Nothing about rebuilding the plugin depends on the pull succeeding, and
# nothing about ingesting the inbox depends on either. So each step runs, each
# reports, and the exit status reflects the whole run.
#
# Deliberately NOT `|| true` on any step: a failure must still surface, or
# launchctl shows green over a stale checkout. A step that cannot run is a
# stall, not a pass.
set -uo pipefail

cd "$(dirname "$0")/.." || exit 2
. scripts/lib/tree-lock.sh || exit 2

FAILED=()
SKIPPED=()

# Test hook: SYNC_FORCE_FAIL="<step> [step...]" makes those steps fail, so the
# independence and exit-status behaviour can be exercised for real rather than
# reasoned about. Takes a list so a LATER step can be shown to be reached
# without actually executing it — kb:auto-ingest makes LLM calls and writes to
# the vault, and triggering that to satisfy a test is not a trade worth making.
# Reaching the simulated branch is itself the proof that control flow got there.
# Unset in normal operation.
_forced() {
  case " ${SYNC_FORCE_FAIL:-} " in (*" $1 "*) return 0 ;; esac
  return 1
}

step() {
  local name="$1"; shift
  echo "── sync: $name ─────────────────────────────────"
  if _forced "$name"; then
    echo "   (SYNC_FORCE_FAIL=$name — simulating failure)"
    FAILED+=("$name")
    return
  fi
  if "$@"; then
    echo "   ✓ $name"
  else
    echo "   ✗ $name (exit $?) — continuing; later steps do not depend on it"
    FAILED+=("$name")
  fi
}

pull_if_on_main() {
  local branch
  branch="$(git branch --show-current)"
  if [ "$branch" != "main" ]; then
    echo "   on '$branch', not main — skipping pull by design"
    return 0
  fi
  git pull --ff-only origin main
}

# pull and build rewrite the working tree, so they run only while this run
# holds the tree lock (scripts/lib/tree-lock.sh). A Pro gate holds it from
# its pin check through `git bundle`; if it does, they are SKIPPED this hour,
# said so and by whom, not failed: the next hourly run does them. 11-21: a
# build at 18:05:11Z made the 18:05:34Z gate refuse on vendor/gryphon.
# ingest writes the vault only, so it runs either way.
# Can arcus capture right now? (PGM 2026-11-22, after two months of silent
# browser failures.) Writes .athena/state/arcus-health.json for PGM's backstop;
# a failure fails this step only. Same interpreter the plugin picks first.
athena_python() {
  if [ -n "${ATHENA_PYTHON:-}" ]; then echo "$ATHENA_PYTHON"
  elif [ -x "$HOME/.athena/venv/bin/python" ]; then echo "$HOME/.athena/venv/bin/python"
  else echo python3; fi
}
step arcus-health "$(athena_python)" scripts/arcus-health.py

if tree_lock_acquire sync 0; then
  step pull    pull_if_on_main
  step build   npm run build:all
  tree_lock_release
else
  echo "── sync: skipped pull + build: the working tree is held ($(tree_lock_holder)) ──"
  SKIPPED+=(pull build)
fi
step ingest  npm run kb:auto-ingest

echo "════════════════════════════════════════════════"
if [ ${#FAILED[@]} -eq 0 ]; then
  echo "sync: all steps ok${SKIPPED[0]:+ (skipped: ${SKIPPED[*]})}"
  exit 0
fi
echo "sync: FAILED steps: ${FAILED[*]}"
echo "  (the other steps still ran — this exit code is what launchctl reports)"
exit 1
