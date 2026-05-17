#!/usr/bin/env bash
# install-to-vault.sh — copy the built Athena + bundled Gryphon plugins
# into an Obsidian vault's .obsidian/plugins/ tree from the current source.
#
# Usage:
#   ./scripts/install-to-vault.sh <vault-path>
#   ./scripts/install-to-vault.sh ~/Documents/test-vault
#   ./scripts/install-to-vault.sh <vault-path> --athena-only
#
# By default installs BOTH plugins (Athena + the bundled Gryphon chat
# surface) since Athena's chat panel delegates to Gryphon — installing
# Athena alone leaves the chat unable to talk to a model.
#
# Pass --athena-only to skip the Gryphon copy (use when the target vault
# already has Gryphon installed via Community Plugins or BRAT).
#
# Safe to re-run to update; creates the destination dirs if missing.
#
# This script is for LOCAL development / maintainer workflow. End users
# should install via Obsidian's Community Plugins directory (once Athena
# is accepted there) or via BRAT (polleoai/athena).

set -euo pipefail

ATHENA_ONLY=0
VAULT=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --athena-only) ATHENA_ONLY=1; shift ;;
    -h|--help)
      sed -n '2,22p' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *)
      if [[ -z "$VAULT" ]]; then
        VAULT="$1"; shift
      else
        echo "install-to-vault: unexpected extra argument: $1" >&2
        exit 1
      fi
      ;;
  esac
done

if [[ -z "$VAULT" ]]; then
  echo "Usage: $0 <vault-path> [--athena-only]" >&2
  echo "Example: $0 ~/Documents/test-vault" >&2
  exit 2
fi

VAULT="${VAULT/#\~/$HOME}"
if [[ ! -d "$VAULT" ]]; then
  echo "Error: vault path does not exist: $VAULT" >&2
  exit 1
fi
if [[ ! -d "$VAULT/.obsidian" ]]; then
  echo "Error: $VAULT doesn't look like an Obsidian vault (no .obsidian/)." >&2
  echo "Open the folder in Obsidian once to initialize, then re-run." >&2
  exit 1
fi

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
REPO_ROOT="$( cd "$SCRIPT_DIR/.." && pwd )"

ATHENA_SRC="$REPO_ROOT/.obsidian/plugins/athena"
GRYPHON_SRC="$REPO_ROOT/.obsidian/plugins/gryphon"

for required in main.js manifest.json styles.css; do
  if [[ ! -f "$ATHENA_SRC/$required" ]]; then
    echo "Error: missing Athena build artifact: $ATHENA_SRC/$required" >&2
    echo "Run 'npm run build:all' first (or 'npm run build' if the bundled Gryphon is already current)." >&2
    exit 1
  fi
done

if [[ "$ATHENA_ONLY" == "0" ]]; then
  for required in main.js manifest.json styles.css; do
    if [[ ! -f "$GRYPHON_SRC/$required" ]]; then
      echo "Error: missing Gryphon build artifact: $GRYPHON_SRC/$required" >&2
      echo "Run 'npm run build:all' first (or 'npm run build:gryphon')." >&2
      echo "(Pass --athena-only if the vault already has Gryphon installed separately.)" >&2
      exit 1
    fi
  done
fi

ATHENA_DST="$VAULT/.obsidian/plugins/athena"
mkdir -p "$ATHENA_DST"
cp "$ATHENA_SRC/main.js"       "$ATHENA_DST/"
cp "$ATHENA_SRC/manifest.json" "$ATHENA_DST/"
cp "$ATHENA_SRC/styles.css"    "$ATHENA_DST/"
echo "Installed Athena into $ATHENA_DST"

if [[ "$ATHENA_ONLY" == "0" ]]; then
  GRYPHON_DST="$VAULT/.obsidian/plugins/gryphon"
  mkdir -p "$GRYPHON_DST"
  cp "$GRYPHON_SRC/main.js"       "$GRYPHON_DST/"
  cp "$GRYPHON_SRC/manifest.json" "$GRYPHON_DST/"
  cp "$GRYPHON_SRC/styles.css"    "$GRYPHON_DST/"
  if [[ -d "$GRYPHON_SRC/hooks" ]]; then
    rm -rf "$GRYPHON_DST/hooks"
    cp -r "$GRYPHON_SRC/hooks" "$GRYPHON_DST/"
  fi
  echo "Installed Gryphon into $GRYPHON_DST"
fi

echo ""
echo "Reload Obsidian (Cmd/Ctrl+R) and enable in Settings → Community plugins."
