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
# Not `mktemp -t`: on macOS it ignores $TMPDIR, and a sandbox may refuse
# /var/folders. Without a log nothing below can tell "ran" from "never ran" (#241).
LOG="$(mktemp "${TMPDIR:-/tmp}/athena-release-tests.XXXXXX")" && [ -w "$LOG" ] ||
  { echo "release-tests: cannot create a log file under ${TMPDIR:-/tmp} — the suite cannot run" >&2; exit 1; }
trap 'rm -f "$LOG"' EXIT
cd "$TREE"
# Python imports server/athena from this tree, not from whichever checkout the
# editable install names (tests/conftest.py does the same for pytest).
export PYTHONPATH="$TREE/server${PYTHONPATH:+:$PYTHONPATH}"
fail=0

echo "=== JavaScript tests (node --test) ==="
js=(tests/test-*.js)
if [ ! -e "${js[0]}" ]; then echo "release-tests: no tests/test-*.js found" >&2; exit 1; fi
node --test --test-reporter=tap "${js[@]}" 2>&1 | tee "$LOG"
jsrc=${PIPESTATUS[0]}
# "tests >= 1" is not enough: Node counts a test file that defines nothing as
# one passing test (its top-level TAP line names the file), and a file whose
# tests are all skipped or todo exits 0 with "pass 0" (voiceagent, via PGM,
# 2026-11-17). So: real passes >= 1, nothing skipped or todo (no athena test
# skips), and no file that ran without defining a test.
js_count() { sed -nE "s/^(# |ℹ )$1 ([0-9]+)\$/\2/p" "$LOG" | tail -1; }
js_pass="$(js_count pass)"; js_skip="$(js_count skipped)"; js_todo="$(js_count todo)"
js_empty="$(grep -E '^ok [0-9]+ - .*test-[^ /]*\.js$' "$LOG" | sed -E 's/^ok [0-9]+ - //' | tr '\n' ' ')"
if ! grep -Eq '^(# |ℹ )tests [1-9][0-9]*$' "$LOG" || [ -z "$js_pass" ] || [ "$js_pass" -lt 1 ]; then
  echo "[js] FAIL: node --test reported no tests run"; fail=1
elif [ -n "$js_empty" ]; then
  echo "[js] FAIL: test file(s) defined no tests: $js_empty"; fail=1
elif [ "${js_skip:-0}" -ne 0 ] || [ "${js_todo:-0}" -ne 0 ]; then
  echo "[js] FAIL: skipped ${js_skip:-0}, todo ${js_todo:-0}: a skipped test did not test anything"; fail=1
elif [ "$jsrc" -eq 0 ]; then echo "[js] OK: ${#js[@]} files, $js_pass passed"; else echo "[js] FAIL"; fail=1; fi

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
# The summary line ("N passed, M failed in X.XXs") proves pytest ran; without
# it, finding no FAILED lines means nothing, not zero failures.
if ! grep -Eq '[0-9]+ (passed|failed|errors?|skipped|xfailed|xpassed|deselected|warnings?)(,.*)? in [0-9.]+s' "$LOG"; then
  echo "[py] FAIL: no pytest summary line — the suite did not run"; cat "$LOG"; exit 1
fi
# Same class as the JS check above: an all-skipped run exits 0 with a summary
# ("5 skipped in 0.1s"), and an emptied test file is collected as nothing.
py_passed="$(grep -Eo '[0-9]+ passed' "$LOG" | tail -1 | cut -d' ' -f1)"
if [ -z "$py_passed" ] || [ "$py_passed" -lt 1 ]; then
  echo "[py] FAIL: pytest passed no tests — the suite did not test anything"; exit 1
fi
py_files=$(ls tests/test_*.py 2>/dev/null | sort -u)
py_hit=$(python3 -m pytest tests/ --collect-only -q -p no:cacheprovider 2>/dev/null | sed -nE 's/^(tests\/test_[^:]*\.py)::.*/\1/p' | sort -u)
py_empty=$(comm -23 <(echo "$py_files" | sed '/^$/d') <(echo "$py_hit" | sed '/^$/d'))
if [ -n "$py_empty" ]; then
  echo "[py] FAIL: test file(s) collected no tests:"; echo "$py_empty" | sed 's/^/  /'; fail=1
fi
got=$(grep -E '^(FAILED|ERROR) ' "$LOG" | sed -E 's/^(FAILED|ERROR) ([^ ]+).*/\2/' | sort -u)
if [ "$rc" -eq 1 ] && [ -z "$got" ]; then
  echo "[py] FAIL: pytest reported failures but none could be read from its output"; cat "$LOG"; exit 1
fi
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
