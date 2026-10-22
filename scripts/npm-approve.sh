#!/usr/bin/env bash
# npm-approve.sh — the maintainer's step after a release: publish the
# @polleoai/athena version the Release workflow staged on npm.
#
#   scripts/npm-approve.sh [version]   default: manifest.json's version
#
# Mirrors Gryphon's scripts/npm-approve.sh, for one package. Reads the staged
# id from that release tag's npm-stage job log and approves it through
# scripts/npm-2fa.sh, so it also runs from an agent's shell: the approval page
# opens in the browser and the maintainer clicks Approve. Uses the saved npm
# login; if there is none, it says so and stops. A version already published
# is reported and skipped.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VERSION="${1:-$(node -p "require('$ROOT/manifest.json').version")}"
REPO=polleoai/athena
NAME=@polleoai/athena

if npm view "$NAME@$VERSION" version >/dev/null 2>&1; then echo "npm-approve: $NAME@$VERSION is already published — nothing to do"; exit 0; fi

RUN=$(gh run list -R "$REPO" --workflow release.yml --branch "$VERSION" --event push -L 1 --json databaseId --jq '.[0].databaseId')
[ -n "$RUN" ] || { echo "npm-approve: no Release run for tag $VERSION on $REPO" >&2; exit 1; }
STAGE=$(gh run view "$RUN" -R "$REPO" --json jobs --jq '.jobs[] | select(.name=="npm-stage") | .conclusion')
[ "$STAGE" = "success" ] || { echo "npm-approve: npm-stage in run $RUN is '${STAGE:-missing}', not success — nothing to approve" >&2; exit 1; }
id=$(gh run view "$RUN" -R "$REPO" --log | grep -o "+ $NAME@$VERSION (staged with id [0-9a-f-]*)" | grep -o '[0-9a-f]\{8\}-[0-9a-f-]*' | head -1 || true)
[ -n "$id" ] || { echo "npm-approve: no staged id for $NAME@$VERSION in run $RUN" >&2; exit 1; }

echo "npm-approve: approving $NAME@$VERSION (staged $id, run $RUN)"
"$ROOT/scripts/npm-2fa.sh" stage approve "$id"
# npm processes a new version for a few minutes before it can be installed.
for _ in $(seq 60); do
  npm view "$NAME@$VERSION" version --prefer-online >/dev/null 2>&1 && break
  sleep 10
done
npm view "$NAME@$VERSION" version --prefer-online >/dev/null 2>&1 || { echo "npm-approve: approved, but $NAME@$VERSION is still not on the registry after 10 minutes" >&2; exit 1; }
echo "npm-approve: $NAME@$VERSION is published"
