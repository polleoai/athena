#!/usr/bin/env bash
# deploy-to-vm.sh — package the built Athena + bundled Gryphon plugins
# and serve them over HTTP so a Linux or Windows VM can pull and install
# in one paste. Use when shared folders aren't set up, or between quick
# fix-rebuild-test cycles where copying main.js by hand gets tedious.
#
# Usage:
#   ./scripts/deploy-to-vm.sh              # rebuild + package + serve
#   ./scripts/deploy-to-vm.sh --no-build   # reuse existing built bundle
#   ./scripts/deploy-to-vm.sh --port 9000  # non-default port
#   ./scripts/deploy-to-vm.sh --athena-only
#
# After the server starts, paste the printed Linux or Windows one-liner
# inside the VM. Ctrl+C on the host to stop the server.
#
# The default tarball includes BOTH plugin directories (Athena +
# Gryphon) since Athena's chat panel delegates to Gryphon — installing
# Athena alone leaves the chat unable to talk to a model. Pass
# --athena-only to ship just the Athena bundle (useful if the VM
# already has Gryphon installed via Community Plugins or BRAT).

set -euo pipefail

cd "$(dirname "$0")/.."

PORT=8000
BUILD=1
ATHENA_ONLY=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --no-build)    BUILD=0; shift ;;
    --port)        PORT="$2"; shift 2 ;;
    --athena-only) ATHENA_ONLY=1; shift ;;
    -h|--help)
      sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *)
      echo "deploy-to-vm: unknown argument: $1" >&2
      echo "Run '$0 --help' for usage." >&2
      exit 1
      ;;
  esac
done

# stat -f%z (macOS) vs stat -c%s (Linux) — this script usually runs on
# macOS but the dual-form keeps it portable for anyone running it
# elsewhere.
_fsize() {
  if stat -f%z "$1" >/dev/null 2>&1; then stat -f%z "$1"
  else stat -c%s "$1"
  fi
}

if [[ "$BUILD" == "1" ]]; then
  if [[ "$ATHENA_ONLY" == "1" ]]; then
    echo "==> npm run build (Athena bundle only)"
    npm run build >/dev/null
  else
    echo "==> npm run build:gryphon && npm run build (full bundle)"
    npm run build:gryphon >/dev/null
    npm run build >/dev/null
  fi
fi

ATHENA_SRC=".obsidian/plugins/athena"
GRYPHON_SRC=".obsidian/plugins/gryphon"

for f in main.js manifest.json styles.css; do
  [[ -f "$ATHENA_SRC/$f" ]] || { echo "deploy-to-vm: missing $ATHENA_SRC/$f" >&2; exit 1; }
done
if [[ "$ATHENA_ONLY" == "0" ]]; then
  for f in main.js manifest.json styles.css; do
    [[ -f "$GRYPHON_SRC/$f" ]] || { echo "deploy-to-vm: missing $GRYPHON_SRC/$f" >&2; exit 1; }
  done
fi

# Stage the bundle as a sibling pair of plugin dirs so the VM extract
# reproduces the .obsidian/plugins/ layout exactly. The tar root is
# `plugins/` so the VM untars directly into .obsidian/.
STAGE=$(mktemp -d -t athena-deploy-XXXXXX)
trap 'rm -rf "$STAGE"' EXIT
mkdir -p "$STAGE/plugins/athena"
cp "$ATHENA_SRC/main.js"       "$STAGE/plugins/athena/"
cp "$ATHENA_SRC/manifest.json" "$STAGE/plugins/athena/"
cp "$ATHENA_SRC/styles.css"    "$STAGE/plugins/athena/"
if [[ "$ATHENA_ONLY" == "0" ]]; then
  mkdir -p "$STAGE/plugins/gryphon"
  cp "$GRYPHON_SRC/main.js"       "$STAGE/plugins/gryphon/"
  cp "$GRYPHON_SRC/manifest.json" "$STAGE/plugins/gryphon/"
  cp "$GRYPHON_SRC/styles.css"    "$STAGE/plugins/gryphon/"
  if [[ -d "$GRYPHON_SRC/hooks" ]]; then
    cp -r "$GRYPHON_SRC/hooks" "$STAGE/plugins/gryphon/"
  fi
fi

TARBALL=/tmp/athena-plugin.tar.gz
SUFFIX="Athena + Gryphon"
[[ "$ATHENA_ONLY" == "1" ]] && SUFFIX="Athena only"
echo "==> packaging: $SUFFIX"
(cd "$STAGE" && tar --no-xattrs -czf "$TARBALL" plugins/)
echo "    wrote $TARBALL ($(_fsize "$TARBALL") bytes)"

# Best-effort host-IP guess for the Windows hint line. The Linux hint
# uses `ip route` to self-discover, so this value isn't critical there.
HOST_IP=""
if command -v ipconfig >/dev/null 2>&1; then
  HOST_IP=$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || echo "")
fi
[[ -z "$HOST_IP" ]] && HOST_IP=$(ifconfig 2>/dev/null | awk '/inet 192\./ {print $2; exit}')
[[ -z "$HOST_IP" ]] && HOST_IP="<host-ip>"

cat <<EOF

==> HTTP server on :$PORT  (host IP: $HOST_IP). Ctrl+C to stop.

────────── Linux VM (Debian / Ubuntu) ──────────
Prereq: a test vault exists at ~/athena-test-vault/ with an
Obsidian-initialized .obsidian/ folder (open the folder in Obsidian
once if you haven't).

Paste as ONE line (terminal pasting often merges newlines anyway,
so we use && between steps to fail fast):

HOST_IP=\$(ip route | awk '/default/ { print \$3 }') && cd ~/athena-test-vault/.obsidian && curl -fSL http://\$HOST_IP:$PORT/athena-plugin.tar.gz -o /tmp/a.tar.gz && tar xzf /tmp/a.tar.gz --overwrite && rm /tmp/a.tar.gz && ls -l plugins/athena/main.js plugins/gryphon/main.js 2>/dev/null

────────── Windows VM (PowerShell) ──────────
Prereq: a test vault exists at %USERPROFILE%\athena-test-vault\ with
an Obsidian-initialized .obsidian\ folder.

Paste as ONE line. PowerShell collapses pasted newlines into spaces
which corrupts multi-line commands; semicolons keep it a valid
statement sequence regardless of how the paste lands. The \${HOST_IP}
braces are required — bare \$HOST_IP:$PORT would trip PS's
colon-as-scope syntax and collapse the URL.

\$HOST_IP = '$HOST_IP'; \$DOTOBS = "\$env:USERPROFILE\athena-test-vault\.obsidian"; New-Item -ItemType Directory -Force -Path \$DOTOBS | Out-Null; Set-Location \$DOTOBS; Invoke-WebRequest "http://\${HOST_IP}:$PORT/athena-plugin.tar.gz" -OutFile a.tar.gz; tar xzf a.tar.gz; Remove-Item a.tar.gz; Get-ChildItem plugins\athena\main.js,plugins\gryphon\main.js -ErrorAction SilentlyContinue | Format-List Name,Length,LastWriteTime

If the printed host IP doesn't work from the VM, find yours with
'ipconfig | findstr Gateway' and use the Default Gateway IP — that's
your host machine from the VM's view under most NAT-style VM networks.

After the file lands in the VM: in Obsidian, Settings → Community
plugins → toggle Athena (and Gryphon if applicable) off, then on.
The new main.js picks up on re-enable.
──────────────────────────────────────────────
EOF

cd /tmp
exec python3 -m http.server "$PORT" --bind 0.0.0.0
