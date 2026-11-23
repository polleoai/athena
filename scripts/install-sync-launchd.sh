#!/usr/bin/env bash
# install-sync-launchd.sh — install both Athena launchd jobs:
#   1. com.athena.sync       — hourly git pull + build + ingest backstop
#   2. com.athena.autoingest — filesystem-watch capture → wiki synth (~1-2s)
#
# Copies the version-controlled plists from scripts/ to ~/Library/LaunchAgents/
# and loads them. Idempotent: existing loads are replaced cleanly.
#
# Uninstall:
#   launchctl unload ~/Library/LaunchAgents/com.athena.sync.plist
#   launchctl unload ~/Library/LaunchAgents/com.athena.autoingest.plist
#   rm ~/Library/LaunchAgents/com.athena.{sync,autoingest}.plist

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VAULT="$(cd "$SCRIPT_DIR/.." && pwd)"
LAUNCHAGENTS="$HOME/Library/LaunchAgents"

# The plists in scripts/ are templates: __ATHENA_VAULT__ is this vault and
# __HOME__ is $HOME, filled in here, so no one user's paths ship (2026-11-23).
render_plist() {  # render_plist <src> <dst>
  local v h
  v="$(printf '%s' "$VAULT" | sed -e 's/[&|\\]/\\&/g')"
  h="$(printf '%s' "$HOME" | sed -e 's/[&|\\]/\\&/g')"
  sed -e "s|__ATHENA_VAULT__|$v|g" -e "s|__HOME__|$h|g" "$1" > "$2"
}

# --render-only <dir>: write the filled-in plists there and stop (tests).
if [ "${1:-}" = "--render-only" ]; then
  mkdir -p "$2"
  for label in com.athena.sync com.athena.autoingest; do
    render_plist "$SCRIPT_DIR/$label.plist" "$2/$label.plist"
  done
  exit 0
fi
mkdir -p "$LAUNCHAGENTS"

install_plist() {
  local label="$1"
  local src="$SCRIPT_DIR/$label.plist"
  local dst="$LAUNCHAGENTS/$label.plist"

  if [ ! -f "$src" ]; then
    echo "ERROR: source plist not found at $src" >&2
    return 1
  fi

  if launchctl list | grep -q "$label"; then
    echo "[install] unloading existing $label"
    launchctl unload "$dst" 2>/dev/null || true
  fi

  render_plist "$src" "$dst"
  launchctl load "$dst"
  echo "[install] ✓ $label loaded ($dst)"
}

install_plist com.athena.sync
install_plist com.athena.autoingest

echo
echo "[install] both jobs loaded:"
echo "  com.athena.sync       hourly  → /tmp/athena-sync.log"
echo "  com.athena.autoingest on-change → /tmp/athena-autoingest.log"
echo
echo "[install] verify with:  launchctl list | grep com.athena"
