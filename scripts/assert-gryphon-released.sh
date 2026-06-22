#!/usr/bin/env bash
# assert-gryphon-released.sh — guard for the PUBLIC Athena release path.
#
# The dev auto-fix loop (gryphon-autofix-loop) lands a Gryphon fix on
# jivebug/gryphon-dev and pins vendor/gryphon to that dev commit. That is
# correct for DEV iteration, but a PUBLIC Athena release must never ship a
# submodule pin a fresh clone / CI / the polleoai curated release cannot
# resolve: the submodule's .gitmodules URL is polleoai/gryphon, so the
# pinned commit MUST exist there (i.e. it must have been RELEASED).
#
# Release is deliberately OUT of the auto-fix loop; this script is the
# safety net for the manual curated release. Run it before cutting a
# public Athena release. Exits non-zero (with a fix hint) on a dev-only pin.
set -uo pipefail

SUB="vendor/gryphon"
PIN="$(git -C "$SUB" rev-parse HEAD 2>/dev/null)" || { echo "✗ cannot read $SUB submodule HEAD"; exit 2; }
echo "pinned $SUB = $PIN"

# origin == polleoai/gryphon (the public release remote AND the .gitmodules URL).
git -C "$SUB" fetch --quiet origin '+refs/heads/*:refs/remotes/origin/*' '+refs/tags/*:refs/tags/*' 2>/dev/null || true

# Resolvable == reachable from ANY polleoai ref. Stable releases land on
# origin/main; prerelease (rc) commits are pushed as a TAG only (not main).
# A fresh-clone 'git submodule update' resolves a pin reachable from either,
# so accept both. A dev-only commit (jivebug/gryphon-dev) is reachable from
# neither.
if [ -n "$(git -C "$SUB" for-each-ref --contains "$PIN" --format='%(refname)' \
            refs/remotes/origin refs/tags 2>/dev/null | head -1)" ]; then
  echo "✓ released — $PIN is reachable from polleoai/gryphon; safe to cut a public Athena release"
  exit 0
fi

echo "✗ REFUSING: $PIN is NOT on polleoai/gryphon — it is a dev-only commit."
echo "  A public release's submodule (.gitmodules → polleoai/gryphon) cannot resolve it,"
echo "  so the release build's 'git submodule update' would fail."
echo "  Re-pin to a RELEASED Gryphon tag first, e.g.:"
echo "    git -C $SUB fetch origin --tags && git -C $SUB checkout <released-tag>   # e.g. 2.4.5"
echo "    git add $SUB && git commit -m 'chore(adopt): pin gryphon to released <tag>'"
exit 1
