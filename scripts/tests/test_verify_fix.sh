#!/usr/bin/env bash
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"; SCRIPT="$HERE/../verify-fix.sh"
out=$(ATHENA_VERIFY_DRYRUN=1 bash "$SCRIPT" jivebug/gryphon-dev fix/issue-5 5 2>&1)
echo "$out" | grep -q "fetch" || { echo "FAIL: no dev fetch"; exit 1; }
echo "$out" | grep -q "consumer-verified\|consumer-failed" || { echo "FAIL: no verdict"; exit 1; }
echo "ALL PASS"
