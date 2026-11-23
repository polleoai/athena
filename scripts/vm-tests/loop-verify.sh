#!/usr/bin/env bash
# loop-verify.sh — consumer verify entry for an npm-file provider (Argus).
# Reads LOOP_PROVIDER_REPO / LOOP_BRANCH / LOOP_ISSUE; runs this consumer's argus
# integration test (the provider's fix is already checked out in the shared copy
# via the file: symlink), then writes the verdict to the provider issue.
set -uo pipefail
REPO="${LOOP_PROVIDER_REPO:?}"; BRANCH="${LOOP_BRANCH:?}"; ISSUE="${LOOP_ISSUE:?}"
LOG="/tmp/loop-verify-${ISSUE}.log"
# The user's rule (2026-11-16): Athena's VM tier runs on the Pro, never the
# Max. On the Max this verify does NOT run: exit 3 with a distinct NOT RUN
# line, and leave the provider issue untouched. A consumer-failed here would
# blame a fix that was never tested; PGM's driver treats exit 3 as a stall.
if ! node "$(dirname "$0")/../lib/pro-only.js" "loop-verify (argus issue $ISSUE)"; then
  echo "LOOP-VERIFY: NOT RUN for $REPO#$ISSUE ($BRANCH): athena's VM tier runs on the Pro, not the Max"
  exit 3
fi
# This consumer's argus integration command (athena vm-tests):
if npm run test:vm 2>&1 | tee "$LOG"; then
  gh issue edit "$ISSUE" -R "$REPO" --add-label consumer-verified --remove-label provider-fixed
  gh issue comment "$ISSUE" -R "$REPO" --body "Fix ${BRANCH} verified by the consumer (argus integration green)."
else
  gh issue edit "$ISSUE" -R "$REPO" --add-label consumer-failed
  gh issue comment "$ISSUE" -R "$REPO" --body "$(printf 'Fix %s FAILED consumer integration.\n\n```\n%s\n```' "$BRANCH" "$(tail -20 "$LOG")")"
fi
