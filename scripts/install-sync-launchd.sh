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
LAUNCHAGENTS="$HOME/Library/LaunchAgents"
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

  cp "$src" "$dst"
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
