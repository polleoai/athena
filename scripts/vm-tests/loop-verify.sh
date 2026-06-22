#!/usr/bin/env bash
# loop-verify.sh — consumer verify entry for an npm-file provider (Argus).
# Reads LOOP_PROVIDER_REPO / LOOP_BRANCH / LOOP_ISSUE; runs this consumer's argus
# integration test (the provider's fix is already checked out in the shared copy
# via the file: symlink), then writes the verdict to the provider issue.
set -uo pipefail
REPO="${LOOP_PROVIDER_REPO:?}"; BRANCH="${LOOP_BRANCH:?}"; ISSUE="${LOOP_ISSUE:?}"
LOG="/tmp/loop-verify-${ISSUE}.log"
# This consumer's argus integration command (athena vm-tests):
if npm run test:vm 2>&1 | tee "$LOG"; then
  gh issue edit "$ISSUE" -R "$REPO" --add-label consumer-verified --remove-label provider-fixed
  gh issue comment "$ISSUE" -R "$REPO" --body "Fix ${BRANCH} verified by the consumer (argus integration green)."
else
  gh issue edit "$ISSUE" -R "$REPO" --add-label consumer-failed
  gh issue comment "$ISSUE" -R "$REPO" --body "$(printf 'Fix %s FAILED consumer integration.\n\n```\n%s\n```' "$BRANCH" "$(tail -20 "$LOG")")"
fi
