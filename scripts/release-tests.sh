#!/usr/bin/env bash
# release-tests.sh — run Athena's FULL test suite on the tree being released.
# Runs before every release tag, in the dev repo AND in the staged public clone
# (the tree you test must be the tree you ship: on 1.7.8 the released tree had
# 9 failures where the dev tree had 6).
#
#   scripts/release-tests.sh [tree]     default: this repo
#
# 1. JavaScript: every tests/test-*.js under `node --test`.
# 2. Python: `pytest tests/`.
#
# Failures already known before a release are listed in tests/known-failures.txt
# (one pytest node id per line, with the issue that tracks it). Any failure NOT
# listed fails this script; a listed test that now passes is reported so the
# list only shrinks. A suite that cannot run is a failure, never a pass.
set -uo pipefail
TREE="$(cd "${1:-$(dirname "$0")/..}" && pwd)"
KNOWN="$TREE/tests/known-failures.txt"
LOG="$(mktemp -t athena-release-tests.XXXXXX)"
trap 'rm -f "$LOG"' EXIT
cd "$TREE"
fail=0

echo "=== JavaScript tests (node --test) ==="
js=(tests/test-*.js)
if [ ! -e "${js[0]}" ]; then echo "release-tests: no tests/test-*.js found" >&2; exit 1; fi
if node --test "${js[@]}"; then echo "[js] OK: ${#js[@]} files"; else echo "[js] FAIL"; fail=1; fi

echo "=== Python tests (pytest) ==="
python3 -m pytest --version >/dev/null 2>&1 || { echo "release-tests: pytest is not installed for python3 — the suite cannot run" >&2; exit 1; }
python3 -m pytest tests/ -q -rfE -p no:cacheprovider >"$LOG" 2>&1
rc=$?
tail -3 "$LOG"
# rc 0 = all passed, 1 = some failed; anything else (collection error,
# interrupted, usage error, no tests) means the suite did not really run.
if [ "$rc" -ne 0 ] && [ "$rc" -ne 1 ]; then
  echo "[py] FAIL: pytest exited $rc — the suite did not complete"; cat "$LOG"; exit 1
fi
got=$(grep -E '^(FAILED|ERROR) ' "$LOG" | sed -E 's/^(FAILED|ERROR) ([^ ]+).*/\2/' | sort -u)
known=$( [ -f "$KNOWN" ] && grep -v -E '^\s*(#|$)' "$KNOWN" | awk '{print $1}' | sort -u )
new=$(comm -23 <(echo "$got" | sed '/^$/d') <(echo "$known" | sed '/^$/d'))
fixed=$(comm -13 <(echo "$got" | sed '/^$/d') <(echo "$known" | sed '/^$/d'))
if [ -n "$new" ]; then
  echo "[py] FAIL: failures not in tests/known-failures.txt:"; echo "$new" | sed 's/^/  /'; fail=1
else
  echo "[py] OK: no failures beyond the $(echo "$known" | sed '/^$/d' | wc -l | tr -d ' ') known ones"
fi
[ -z "$fixed" ] || { echo "[py] now passing — remove from tests/known-failures.txt:"; echo "$fixed" | sed 's/^/  /'; }

[ "$fail" -eq 0 ] && echo "Release tests PASSED" || echo "Release tests FAILED"
exit "$fail"
