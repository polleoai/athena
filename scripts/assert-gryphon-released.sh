#!/usr/bin/env bash
# assert-gryphon-released.sh — guard for the PUBLIC Athena release path.
#
# A public Athena release must never ship a submodule pin that a fresh clone,
# CI, or the polleoai curated release cannot resolve: .gitmodules points at
# polleoai/gryphon, so the pinned commit MUST exist there (i.e. be RELEASED).
# Dev iteration may pin a jivebug/gryphon-dev commit; this is the safety net
# for the manual curated release. Run it before cutting.
#
# Reads the pin from the PARENT repo's gitlink, never from inside the
# submodule. `git -C vendor/gryphon rev-parse HEAD` searches upward when the
# submodule is an empty directory, finds Athena's .git, and returns ATHENA's
# HEAD — after which the reachability test asks whether Athena's HEAD is
# reachable in Athena, which is always true. The gate then passed precisely
# when it had nothing to check (observed on the 1.7.9 cut, where it printed
# the Athena HEAD sha as "pinned vendor/gryphon").
set -uo pipefail

REPO_SLUG="polleoai/gryphon"

ROOT="$(git rev-parse --show-toplevel 2>/dev/null)" || { echo "✗ not in a git repository"; exit 2; }
cd "$ROOT" || exit 2

# Resolve the submodule path as the repo records it. ls-tree matches pathspecs
# relative to the cwd, so a path built from the cwd can silently miss on a
# case-insensitive filesystem (projects/athena vs Projects/athena).
SUB="$(git config --file .gitmodules --get-regexp '^submodule\..*\.path$' 2>/dev/null \
        | awk '{ $1=""; sub(/^ /,""); print }' | grep -E 'vendor/gryphon$' | head -1)"
[ -n "$SUB" ] || { echo "✗ no vendor/gryphon entry in .gitmodules"; exit 2; }

PIN="$(git ls-tree --full-tree HEAD -- "$SUB" | awk '$1=="160000" && $2=="commit" { print $3 }')"
case "$PIN" in
  [0-9a-f][0-9a-f]*) : ;;
  *) echo "✗ could not read the gitlink for $SUB from HEAD"; exit 2 ;;
esac
echo "pinned $SUB = $PIN"

# Prefer the submodule's own clone when it is genuinely populated. `git -C`
# on an empty directory would walk up to the parent repo, so require a .git
# entry inside it before trusting any command run there.
if [ -e "$SUB/.git" ]; then
  git -C "$SUB" fetch --quiet origin '+refs/heads/*:refs/remotes/origin/*' '+refs/tags/*:refs/tags/*' 2>/dev/null || true
  if [ -n "$(git -C "$SUB" for-each-ref --contains "$PIN" --format='%(refname)' \
              refs/remotes/origin refs/tags 2>/dev/null | head -1)" ]; then
    echo "✓ released — $PIN is reachable from $REPO_SLUG (checked in the submodule clone)"
    exit 0
  fi
  echo "✗ REFUSING: $PIN is NOT reachable from any $REPO_SLUG ref — it looks like a dev-only commit."
  echo "  A public release's submodule (.gitmodules → $REPO_SLUG) could not resolve it."
  echo "  Re-pin to a RELEASED Gryphon tag first:"
  echo "    git -C $SUB fetch origin --tags && git -C $SUB checkout <released-tag>"
  echo "    git add $SUB && git commit -m 'chore(adopt): pin gryphon to released <tag>'"
  exit 1
fi

# Submodule not populated — ask GitHub directly rather than passing blind.
if command -v gh >/dev/null 2>&1; then
  if gh api "repos/$REPO_SLUG/commits/$PIN" --jq '.sha' >/dev/null 2>&1; then
    echo "✓ released — $PIN exists on $REPO_SLUG (checked via the GitHub API; submodule not populated)"
    exit 0
  fi
  echo "✗ REFUSING: $PIN was not found on $REPO_SLUG via the GitHub API."
  echo "  Re-pin to a RELEASED Gryphon tag before cutting a public release."
  exit 1
fi

echo "✗ CANNOT VERIFY: $SUB is not populated and gh is unavailable, so the pin"
echo "  could not be checked against $REPO_SLUG. This is a stall, not a pass."
echo "  Run: git submodule update --init --recursive $SUB"
exit 2
