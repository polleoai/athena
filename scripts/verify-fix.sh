#!/usr/bin/env bash
# verify-fix.sh <provider_repo> <branch> <issue> — stage the provider's fix into
# this consumer (git-submodule), rebuild, integration-gate, write the verdict.
#   ATHENA_VERIFY_DRYRUN=1  print the steps without running them
set -uo pipefail
PROVIDER_REPO="${1:?provider repo required}"; BRANCH="${2:?fix branch required}"; ISSUE="${3:?issue required}"
DEV_URL="git@github.com:${PROVIDER_REPO}.git"
DRY="${ATHENA_VERIFY_DRYRUN:-0}"; LOG="/tmp/consumer-verify-${ISSUE}.log"
say(){ echo "==> $*"; }
# A step that fails BEFORE the integration gate means the consumer could not
# verify at all — that is not a verdict on the fix, so it must not set
# consumer-failed (the loop would re-dispatch the provider). But it must not be
# silent either: say so on the issue, then exit non-zero.
blocked(){
  echo "FAILED: $*" >&2
  if [ "$DRY" != "1" ]; then
    gh issue comment "$ISSUE" -R "$PROVIDER_REPO" --body "$(printf 'Consumer verification for %s **could not run** (no verdict): step failed:\n\n```\n%s\n```\nThe label stays `provider-fixed`; fix the consumer environment and re-run the verify step.' "$BRANCH" "$*")" >/dev/null 2>&1 || true
  fi
  exit 1
}
run(){ if [ "$DRY" = "1" ]; then echo "  (dry) $*"; else "$@" || blocked "$*"; fi; }

say "ensure 'dev' remote on vendor/gryphon"
run bash -c "git -C vendor/gryphon remote get-url dev >/dev/null 2>&1 || git -C vendor/gryphon remote add dev '$DEV_URL'"
say "reset vendor/gryphon build artifacts (it is read-only; only the build touches it)"
# build:gryphon runs `npm install`, which rewrites the tracked lockfile's root
# version. Left behind, that blocks checking out the next fix branch.
run git -C vendor/gryphon checkout -- package-lock.json
if [ "$DRY" != "1" ] && [ -n "$(git -C vendor/gryphon status --porcelain --untracked-files=no)" ]; then
  blocked "unexpected local changes in vendor/gryphon (it must stay pristine): $(git -C vendor/gryphon status --porcelain --untracked-files=no | head -5 | tr '\n' ' ')"
fi
# The verify moves vendor/gryphon onto the fix branch and rebuilds, which
# REPLACES the installed plugin in .obsidian/plugins/gryphon with a build of
# unreleased dev code. Nothing here used to put either back, so the user's
# Obsidian ran the fix branch until some later job happened to reset it.
#
# Restore on EVERY exit path — success verdict, failure verdict, blocked()'s
# early exit, and anything unexpected — hence a trap rather than a tail call.
# Idempotent: harmless if the submodule is already at the pin.
#
# Armed here, immediately before the first mutation, so the paths above it
# (which have not moved anything) do not pay for a rebuild.
restore_pin() {
  local rc=$?
  if [ "$DRY" = "1" ]; then
    echo "==> (dry) restore: submodule -> pin, reinstall pinned plugin"
    exit "$rc"
  fi
  say "restore: vendor/gryphon -> recorded pin, reinstall the pinned plugin"
  local rfail=0
  # build:gryphon's npm install rewrites the tracked lockfile; clear it first
  # or the checkout below refuses. Not `|| true`: a failure here is reported.
  git -C vendor/gryphon checkout -- package-lock.json || rfail=1
  # No pathspec: a relative one is matched against --show-prefix, which reports
  # this filesystem's real casing and never matches the index (see 14f8dc6).
  git submodule update --init --recursive || rfail=1
  npm run build:gryphon || rfail=1
  if [ "$rfail" -ne 0 ]; then
    echo "!! RESTORE FAILED — vendor/gryphon and/or .obsidian/plugins/gryphon may still hold ${BRANCH}" >&2
    echo "!! the user's Obsidian may be running unreleased code; recover with:" >&2
    echo "!!   git submodule update --init --recursive && npm run build:gryphon" >&2
    exit 1
  fi
  say "restored: installed plugin now $(grep -m1 '"version"' .obsidian/plugins/gryphon/manifest.json | tr -d ' ,\"' | sed 's/version://')"
  exit "$rc"
}
trap restore_pin EXIT

# Self-test hook: arms the trap and exits, so the REAL restore code can be
# exercised against a submodule deliberately moved off the pin. A dry run
# cannot vouch for the lines it skips, and the full script cannot be used as a
# test because its verdict block comments on a live GitHub issue.
if [ "${ATHENA_VERIFY_SELFTEST:-0}" = "1" ]; then
  say "selftest: arming restore only, skipping verify"
  exit 0
fi

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
