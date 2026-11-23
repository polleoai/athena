#!/usr/bin/env bash
# pro-matrix-payload.sh: Athena's linux-vm + windows-vm matrix, run on the Pro
# by argus scripts/pro-gui-run.sh (--script this --vms "Debian 11 (Xfce);Windows").
#
# pro-gui-run gives this script:
#   RUN_DIR/athena      the bundled athena commit, with vendor/gryphon at its gitlink
#   ARGUS_DIR           argus at the pinned sha
#   UTMCTL              the Pro's utmctl
#   EVIDENCE_DIR        copied back to the Max
#
# What it runs, all inside the run copy, which is removed with the run:
#   1. npm ci at the athena root and in scripts/vm-tests (from the committed
#      lockfiles), then `npm install --no-save $ARGUS_DIR`. PGM approved these
#      installs on 11-20. The committed file: path to argus doesn't resolve
#      under the run dir, so the pinned argus is installed in its place.
#      First, `npm ci --omit=dev` inside ARGUS_DIR (PGM, 11-21): a folder
#      outside the project root is only LINKED by npm, its own dependencies
#      never installed, and argus's files resolve express from ARGUS_DIR. Run
#      -1 died on exactly that ("Cannot find module 'express'"). Before vm-run,
#      every dependency argus lists must resolve from ARGUS_DIR, or 86.
#   2. The plugin build, on the user's 11-20 choice A ("build in the Pro run
#      copy only"). These are build:all's two build steps. Its first step,
#      `git submodule update`, is left out: pro-gui-run has already checked out
#      vendor/gryphon at the gitlink (R4b), and that step would fetch.
#   3. argus vm-run --no-build --targets linux-vm,windows-vm.
#
# Exit codes: 86 means a precondition failed before anything was measured
# (pro-gui-run's convention). Otherwise vm-run's own exit code is returned,
# never masked. Evidence (logs, the argus result dir and its .progress
# durations) is copied either way.
set -uo pipefail

pre() { echo "pro-matrix-payload: precondition failed: $*" >&2; exit 86; }

for v in RUN_DIR ARGUS_DIR EVIDENCE_DIR UTMCTL; do
  [ -n "${!v:-}" ] || pre "$v is not set"
done
A="$RUN_DIR/athena"
[ -f "$A/package.json" ] || pre "no athena checkout at $A"
[ -f "$A/vendor/gryphon/package.json" ] || pre "no vendor/gryphon in the athena checkout"
[ -d "$ARGUS_DIR" ] || pre "no argus at $ARGUS_DIR"
mkdir -p "$EVIDENCE_DIR" || pre "cannot create $EVIDENCE_DIR"
RESULTS="${ARGUS_RESULTS_DIR:-/tmp/argus-results}"   # argus.config.js resultsDir (override: tests)
step() { echo "[$(date -u +%FT%TZ)] $*" | tee -a "$EVIDENCE_DIR/payload-steps.log"; }

[ -f "$ARGUS_DIR/package-lock.json" ] || pre "no package-lock.json in $ARGUS_DIR"

step "install: argus's runtime dependencies (in ARGUS_DIR)"
(cd "$ARGUS_DIR" && PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1 npm ci --omit=dev) \
  > "$EVIDENCE_DIR/npm-ci-argus.log" 2>&1 || pre "npm ci in argus (see npm-ci-argus.log)"
# What the VMs get as argus/: the pinned commit's tracked files only, never
# ARGUS_DIR itself. ARGUS_DIR now holds a macOS-built node_modules (above),
# and argus's bundler ships every file under its src. The guests install
# argus's deps themselves (`npm install ./argus`, from the registry).
BUNDLE_ARGUS="$RUN_DIR/argus-bundle-src"
step "argus for the VM bundle: tracked files of $(git -C "$ARGUS_DIR" rev-parse --short=12 HEAD 2>/dev/null || echo '?')"
rm -rf "$BUNDLE_ARGUS" && mkdir -p "$BUNDLE_ARGUS" \
  && git -C "$ARGUS_DIR" archive --format=tar HEAD | tar -x -C "$BUNDLE_ARGUS" \
  || pre "git archive of the pinned argus into $BUNDLE_ARGUS"
[ -f "$BUNDLE_ARGUS/package.json" ] && [ ! -e "$BUNDLE_ARGUS/node_modules" ] \
  || pre "the argus bundle copy is not a clean tracked-only tree"
step "install: athena root"
(cd "$A" && npm ci) > "$EVIDENCE_DIR/npm-ci-root.log" 2>&1 || pre "npm ci at the athena root (see npm-ci-root.log)"
step "install: scripts/vm-tests and the pinned argus"
(cd "$A/scripts/vm-tests" && npm ci && npm install --no-save "$ARGUS_DIR") \
  > "$EVIDENCE_DIR/npm-ci-vm-tests.log" 2>&1 || pre "vm-tests install (see npm-ci-vm-tests.log)"

step "build: gryphon, then athena (option A, run copy only)"
(cd "$A" && npm run build:gryphon && npm run build) > "$EVIDENCE_DIR/build.log" 2>&1 \
  || pre "plugin build (see build.log)"
for f in .obsidian/plugins/athena/main.js .obsidian/plugins/gryphon/main.js; do
  [ -s "$A/$f" ] || pre "build left no $f"
done

# Every runtime dependency argus declares must resolve from argus itself.
node -e '
  const dir = process.argv[1], p = require(dir + "/package.json"), miss = [];
  for (const d of Object.keys(p.dependencies || {})) {
    try { require.resolve(d + "/package.json", { paths: [dir] }); }
    catch { try { require.resolve(d, { paths: [dir] }); } catch { miss.push(d); } }
  }
  if (miss.length) { console.error("unresolved from argus: " + miss.join(", ")); process.exit(1); }
' "$ARGUS_DIR" 2>> "$EVIDENCE_DIR/payload-steps.log" || pre "argus dependencies don't resolve from $ARGUS_DIR"

# Rehearsal (PGM, 11-21): everything above ran for real; stop before the first
# VM boot. vm-run's own pre-boot code (config, artifact check, bundle) runs in
# pro-rehearse.js; no VM is booted. Its exit code is returned.
if [ "${PAYLOAD_STOP_BEFORE_BOOT:-0}" = 1 ]; then
  step "rehearsal: argus pre-boot steps, then stop before the first VM boot"
  (cd "$A/scripts/vm-tests" && ARGUS_REPO="$BUNDLE_ARGUS" node pro-rehearse.js "$ARGUS_DIR" "$RUN_DIR" "$EVIDENCE_DIR" "$UTMCTL") \
    > "$EVIDENCE_DIR/rehearsal.log" 2>&1
  rc=$?
  step "rehearsal exit=$rc: $(tail -1 "$EVIDENCE_DIR/rehearsal.log")"
  exit "$rc"
fi

before="$(ls -1 "$RESULTS" 2>/dev/null | sort)"
step "vm-run: linux-vm, windows-vm"
start=$(date +%s)
# ARGUS_REPO: what argus.config.js bundles into the VMs as argus/. Its default
# is the Max's layout, which doesn't exist here (run -2: ENOENT).
(cd "$A/scripts/vm-tests" && ARGUS_REPO="$BUNDLE_ARGUS" npx argus vm-run --no-build --targets linux-vm,windows-vm) \
  > "$EVIDENCE_DIR/vm-run.log" 2>&1
rc=$?
step "vm-run exit=$rc after $(( $(date +%s) - start ))s"

# The new result dir(s) from this run, with their .progress (durations).
after="$(ls -1 "$RESULTS" 2>/dev/null | sort)"
new="$(comm -13 <(printf '%s\n' "$before") <(printf '%s\n' "$after") | sed '/^$/d')"
if [ -z "$new" ]; then
  step "no new result dir under $RESULTS"
else
  while IFS= read -r d; do
    cp -R "$RESULTS/$d" "$EVIDENCE_DIR/" && step "evidence: $d"
  done <<< "$new"
fi
exit "$rc"
