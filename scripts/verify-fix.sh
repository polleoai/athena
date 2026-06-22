#!/usr/bin/env bash
# verify-fix.sh <provider_repo> <branch> <issue> — stage the provider's fix into
# this consumer (git-submodule), rebuild, integration-gate, write the verdict.
#   ATHENA_VERIFY_DRYRUN=1  print the steps without running them
set -uo pipefail
PROVIDER_REPO="${1:?provider repo required}"; BRANCH="${2:?fix branch required}"; ISSUE="${3:?issue required}"
DEV_URL="git@github.com:${PROVIDER_REPO}.git"
DRY="${ATHENA_VERIFY_DRYRUN:-0}"; LOG="/tmp/consumer-verify-${ISSUE}.log"
say(){ echo "==> $*"; }
run(){ if [ "$DRY" = "1" ]; then echo "  (dry) $*"; else "$@" || { echo "FAILED: $*" >&2; exit 1; }; fi; }

say "ensure 'dev' remote on vendor/gryphon"
run bash -c "git -C vendor/gryphon remote get-url dev >/dev/null 2>&1 || git -C vendor/gryphon remote add dev '$DEV_URL'"
say "fetch + pin to ${BRANCH}"
run bash -c "git -C vendor/gryphon fetch dev '$BRANCH' && git -C vendor/gryphon checkout FETCH_HEAD"

say "build against the fix (build:gryphon regenerates dist; never build:all)"
run npm run build:gryphon
run npm run build

say "integration gate for issue #$ISSUE"
if [ "$DRY" = "1" ]; then echo "  (dry) ./scripts/release-smoke-test.sh → assume pass"; ok=0
else ./scripts/release-smoke-test.sh 2>&1 | tee "$LOG"; ok=${PIPESTATUS[0]}; fi

if [ "${ok:-1}" -eq 0 ]; then
  say "verdict: consumer-verified"
  run gh issue edit "$ISSUE" -R "$PROVIDER_REPO" --add-label consumer-verified --remove-label provider-fixed
  run gh issue comment "$ISSUE" -R "$PROVIDER_REPO" --body "Fix ${BRANCH} verified by the consumer (build + smoke-test green)."
else
  say "verdict: consumer-failed"
  run gh issue edit "$ISSUE" -R "$PROVIDER_REPO" --add-label consumer-failed
  if [ "$DRY" = "1" ]; then DETAIL="(dry run)"; else DETAIL="$(tail -20 "$LOG" 2>/dev/null)"; fi
  run gh issue comment "$ISSUE" -R "$PROVIDER_REPO" \
    --body "$(printf 'Fix %s FAILED consumer integration.\n\n```\n%s\n```' "$BRANCH" "$DETAIL")"
fi
