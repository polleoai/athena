#!/usr/bin/env bash
# npm-2fa.sh — run an npm command that needs the maintainer's browser 2FA.
#
#   scripts/npm-2fa.sh <npm args...>     e.g. stage approve <id>
#
# npm only offers its browser sign-in when it has a terminal; without one it
# stops with EOTP. Run from a terminal, this is plain npm. Run from anything
# else (an agent's shell, a pipe), npm runs under `script` so it gets a
# terminal, and the npmjs.com approval page it asks for is printed and opened
# in the browser. The maintainer clicks Approve; nothing else is typed.
#
# Needs a saved npm login (`npm login`, once). Exits with npm's status.
set -euo pipefail
NPM=(npx -y npm@11.21.0)
[ $# -gt 0 ] || { echo "usage: scripts/npm-2fa.sh <npm args...>" >&2; exit 2; }

if [ -t 0 ] && [ -t 1 ]; then exec "${NPM[@]}" "$@" --auth-type=web; fi

"${NPM[@]}" whoami >/dev/null 2>&1 || { echo "npm-2fa: not logged in to npm — run: npm login" >&2; exit 1; }

LOG=$(mktemp -t npm-2fa.XXXXXX)
trap 'rm -f "$LOG"' EXIT
# The newline answers npm's "Press ENTER to open in the browser".
( sleep 3; echo ) | script -q "$LOG" "${NPM[@]}" "$@" --auth-type=web >/dev/null 2>&1 &
pid=$!
shown=
while kill -0 "$pid" 2>/dev/null; do
  if [ -z "$shown" ]; then
    url=$(grep -a -o 'https://www\.npmjs\.com/auth/cli/[0-9a-f-]*' "$LOG" | head -1 || true)
    if [ -n "$url" ]; then echo "npm-2fa: approve in the browser: $url"; shown=1; fi
  fi
  sleep 1
done
rc=0; wait "$pid" || rc=$?
# npm's output without the spinner and colour codes.
tr -d '\r' < "$LOG" | sed -e 's/\x1b\[[0-9;]*[A-Za-z]//g' -e 's/[⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏]//g' \
  | grep -a -v -E '^(npm notice [0-9.]+[kMG]?B |Press ENTER|\^D|$)' || true
if [ "$rc" -eq 0 ] && grep -a -q 'npm error' "$LOG"; then rc=1; fi
exit "$rc"
