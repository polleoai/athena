"use strict";

/**
 * argus.config.js — Athena's wiring for the Argus test harness (v1.2.0).
 *
 *   driver   — playwright   (Athena = Obsidian = Electron = CDP)
 *   launcher — obsidian
 *   fixture  — multi-plugin (athena + vendored gryphon) per AF1, seeded inbox
 *   build    — OR-1: declarative build + artifact preflight
 *   resources— OR-4: repo trees exported to specs as env vars (no path math)
 *   targets  — linux-vm + windows-vm; ALL-SSH, default AND net (Argus 1.1): each
 *              `provision` block boots the UTM VM + carries the run over SSH, so
 *              no HTTP server and no macOS firewall step. Idempotent: deps install
 *              into a per-runId workspace argus `rm -rf`s on teardown; the opt-in
 *              RUN_NET_TESTS arcus venv is provisioned via `provision.setup` and
 *              removed via `provision.teardownCmd` (issue #1) — see the targets note.
 *
 * Host smoke:  npm run test:host        (Playwright direct; per-test fixture)
 * VM run:      cd scripts/vm-tests && npx argus vm-run [--targets linux-vm]
 *              → build → bundle → boot VM (utmctl) → scp+run over SSH → collect
 *              argus-result.json → matrix → cleanup (workspace + procs). (Add
 *              --no-provision to skip boot+ssh and print the paste line instead.)
 *
 * Bundle layout is FLAT: argus.config.js sits at the bundle root, which is the
 * `baseDir` the shipped runner resolves (where it writes argus-result.json and
 * runs Playwright from). `resources` dests are bundle-root-relative and match
 * the host-fallback repo paths the specs use when no bundle/env is present.
 */

const path = require("path");

// Athena vault root: scripts/vm-tests → scripts → <root>.
const ATHENA_ROOT = path.resolve(__dirname, "..", "..");
// The argus harness repo — bundled to <bundle-root>/argus so the VM resolves
// @shared/argus offline (package.vm.json → file:argus) and runs the shipped
// runner as `node argus/src/host/vm-run-side.js`.
const ARGUS_REPO = path.resolve(ATHENA_ROOT, "..", "..", "Projects", "argus");

// Built-plugin + resource source dirs on the HOST (built by `npm run
// build:all` into .obsidian/plugins/<id>/). In the VM the shipped runner
// (OR-4) overrides these via the `resources` env vars below.
const HOST_ATHENA_PLUGIN = path.join(ATHENA_ROOT, ".obsidian", "plugins", "athena");
const HOST_GRYPHON_PLUGIN = path.join(ATHENA_ROOT, ".obsidian", "plugins", "gryphon");

// Real-network ingest matrix (05-kb-add-url-matrix): gated by RUN_NET_TESTS=1,
// exactly like host mode (`RUN_NET_TESTS=1 npm run test:host`). When enabled, the
// guest's bootstrap (a single shell, so exported vars persist to the test run)
// PROVISIONS its own Python ≥ 3.11 — arcus's floor — with `uv`, installs the real
// extractor stack into a dedicated venv, and exports ATHENA_PYTHON so kb runs
// under it (pythonCmd / the spec PY const honor that var). This makes the net
// matrix self-bootstrapping: no manual per-guest Python upgrade, on any distro
// whose stock python3 is too old (e.g. Debian 11 = 3.9). RUN_NET_TESTS is also
// propagated into the run so the gated specs activate. OFF by default → normal
// VM runs are byte-unchanged.
//   Run:  RUN_NET_TESTS=1 npm run test:vm:net -- --targets linux-vm,windows-vm
const RUN_NET = !!process.env.RUN_NET_TESTS;
// athena's runtime deps for ingest: arcus (extraction) + yt-dlp (video) PLUS
// athena's own pipeline deps — pydantic (the schema-validated raw/wiki writers)
// and sqlite-vec (search index). An isolated venv has none of these unless we
// install them, so the in-process write pipeline would fail with no raw/wiki.
const ARCUS_DEP =
  'pydantic sqlite-vec "arcus-provider-runtime[html,pdf,office]>=0.6.0" yt-dlp';
// POSIX: install uv → make a 3.12 venv → install arcus into it → Chromium → export.
const NET_SETUP_POSIX =
  "curl -LsSf https://astral.sh/uv/install.sh | sh " +
  '&& export PATH="$HOME/.local/bin:$PATH" ' +
  '&& uv venv --clear --python 3.12 "$HOME/athena-venv" ' +
  '&& export ATHENA_PYTHON="$HOME/athena-venv/bin/python" ' +
  `&& uv pip install --python "$ATHENA_PYTHON" ${ARCUS_DEP} ` +
  '&& "$ATHENA_PYTHON" -m playwright install chromium';
// Windows (PowerShell, one session → $env: persists to the run).
//   $ErrorActionPreference='Continue': uv/yt-dlp/playwright write progress to
//   STDERR, and Windows PowerShell promotes any native-command stderr write to a
//   NativeCommandError that ABORTS the script under the default 'Stop' — so a
//   *successful* `uv venv` (logs "Using CPython …" to stderr) would otherwise kill
//   provisioning right after venv creation, before the pip install + Chromium +
//   the ATHENA_PYTHON export. POSIX `sh` only cares about exit codes, so
//   NET_SETUP_POSIX never hit this. --clear: recreate the venv unconditionally so
//   uv never emits the interactive "already exists [y/n]" prompt (would hang the
//   unattended bootstrap on a guest with a stale athena-venv from a prior run).
const NET_SETUP_WIN =
  "$ErrorActionPreference='Continue'; " +
  "irm https://astral.sh/uv/install.ps1 | iex; " +
  '$env:PATH = "$env:USERPROFILE\\.local\\bin;$env:PATH"; ' +
  // Profile-independent venv path: the guest's profile dir (C:\Users\Tony) does
  // NOT match the SSH login name (debian), and provision.env values are literal
  // (can't expand $env:USERPROFILE) — so a fixed C:\Users\Public path is the only
  // location both `setup` and the env-pinned ATHENA_PYTHON can agree on.
  'uv venv --clear --python 3.12 "C:\\Users\\Public\\athena-venv"; ' +
  '$env:ATHENA_PYTHON = "C:\\Users\\Public\\athena-venv\\Scripts\\python.exe"; ' +
  `uv pip install --python $env:ATHENA_PYTHON ${ARCUS_DEP}; ` +
  "& $env:ATHENA_PYTHON -m playwright install chromium";
// NET_SETUP_WIN is PowerShell, but argus runs a Windows `provision.setup` via
// `cmd /c`. Argus 1.2's `setupShell: "powershell"` does the UTF-16LE base64 +
// `powershell -EncodedCommand` host-side, so we just pass NET_SETUP_WIN raw and
// set setupShell on the target — no consumer-side encoding (issue #1 / SX14).
// Run-scoped teardown (Argus 1.1 provision.teardownCmd). Removes ONLY what the
// net setup created — the venv + uv binary/data — and deliberately spares the
// shared caches (npm, ms-playwright) so repeat runs stay fast. teardownCmd runs
// as a direct SSH command, so it is interpreted by the guest's login shell:
// /bin/sh on Linux, PowerShell on the Windows guest (NOT cmd) — hence the two
// dialects below.
const NET_TEARDOWN_POSIX =
  "rm -rf ~/athena-venv ~/.local/bin/uv ~/.local/bin/uvx ~/.local/share/uv ~/.cache/uv";
const NET_TEARDOWN_WIN =
  "Remove-Item -Recurse -Force " +
  "\"C:\\Users\\Public\\athena-venv\",\"$env:USERPROFILE\\.local\\bin\\uv*\",\"$env:LOCALAPPDATA\\uv\" " +
  "-ErrorAction SilentlyContinue";

module.exports = {
  driver: "playwright",
  launcher: "obsidian",

  // ── OR-1: build + artifact preflight ──────────────────────────────
  // `argus vm-run` runs `command` (skipped by --no-build) then verifies
  // `artifacts` exist before bundling. cwd is "../.." because the operator
  // runs `npx argus vm-run` from scripts/vm-tests (where this config +
  // package.json live), and build:all must run at the repo root.
  build: {
    command: "npm run build:all",
    cwd: "../..",
    artifacts: [
      ".obsidian/plugins/athena/main.js",
      ".obsidian/plugins/gryphon/main.js",
    ],
  },

  // ── OR-4: resource indirection ────────────────────────────────────
  // The shipped runner exports each NAME as an env var pointing at the
  // BUNDLED location (bundle-root + relPath). Specs + the fixture below read
  // process.env.<NAME> with a host fallback, so the SAME runner drives this
  // (2 plugins + bin/ + a vendored protect/ tree) with no `../../..` math.
  resources: {
    ATHENA_PLUGIN_DIR: ".obsidian/plugins/athena",
    GRYPHON_PLUGIN_DIR: ".obsidian/plugins/gryphon",
    ATHENA_BIN_DIR: "bin", // specs spawn <vault>/bin/kb (AT-3/AT-11 + 06)
    GRYPHON_PROTECT_DIR: "vendor/gryphon/packages/protect", // 07-guardrail
  },

  // ── AF1 multi-plugin + AF3 seedFiles ──────────────────────────────
  fixture: {
    plugins: [
      {
        // Gryphon first — athena's chat view extends GryphonChatView.
        id: "gryphon",
        sourceDir: process.env.GRYPHON_PLUGIN_DIR || HOST_GRYPHON_PLUGIN,
        data: {
          provider: "anthropic-api",
          model: "claude-opus-4-8",
          anthropicApiKey: "mock-key-for-tests",
          openaiApiKey: "mock-key-for-tests",
          googleApiKey: "mock-key-for-tests",
          permissionMode: "default",
          protectedMode: "deny-with-modal",
        },
      },
      {
        id: "athena",
        sourceDir: process.env.ATHENA_PLUGIN_DIR || HOST_ATHENA_PLUGIN,
        data: { claudePath: "" },
      },
    ],
    obsidianConfig: { promptDelete: false },
  },

  // ── Axis 2: VM targets — Argus 1.1 all-SSH, idempotent (incl. net) ──
  // VM-image prereqs: Obsidian + Node 18+ + python3 in a graphical, auto-login
  // session, with sshd up and the host's key authorized for the `debian` user.
  //
  // ALWAYS PURE SSH (no HTTP, no macOS firewall) — default AND net. Each target's
  // `provision` block boots the named UTM VM via utmctl, then carries the FULL run
  // host-initiated over SSH: boot → (RUN_NET only: `setup` provisions the arcus
  // venv) → scp the bundle into a FRESH per-runId workspace → `driverInstall`
  // (npm install) → `runCmd` (vm-run-side.js, env-injected, headed via DISPLAY on
  // Linux) → scp argus-result.json back. Every target selects the SSH transport,
  // so argus starts NO inbound HTTP server and skips the firewall preflight
  // (vm-run.js `if (anyHttp)`). Run: `cd scripts/vm-tests && npx argus vm-run`.
  //
  // IDEMPOTENT: argus always runs cleanup — `rm -rf <workspace>` (every npm dep
  // from driverInstall) + `teardownCmd` (the net venv/uv, RUN_NET only) + kill
  // `killOnCleanup`. `teardown: "restore"` leaves already-running VMs up. The
  // guest is left exactly as found; shared npm + ms-playwright caches persist.
  //
  // NET MATRIX (opt-in, RUN_NET_TESTS=1) NOW RUNS ALL-SSH (Argus 1.1, issue #1):
  // `provision.setup` runs NET_SETUP_* over SSH before driverInstall, and
  // `provision.teardownCmd` removes the venv/uv on cleanup. POSIX: setup shares
  // the run shell, so its `export ATHENA_PYTHON` persists. WINDOWS: setup runs in
  // a `cmd /c` subshell (vars don't persist) → ATHENA_PYTHON is pinned in env, and
  // NET_SETUP_WIN is plain PowerShell run via `setupShell: "powershell"` (Argus 1.2
  // encodes it host-side). The venv lives at a fixed C:\Users\Public path because
  // the guest profile dir (C:\Users\Tony) ≠ the SSH login (debian).
  targets: {
    "linux-vm": {
      provision: {
        provider: "utm", vm: "Debian 11 (Xfce)", teardown: "restore",
        transport: "ssh",
        ssh: { user: "debian", host: "auto" },   // host:auto → utmctl ip-address (192.168.64.2)
        display: ":0",                            // headed Electron on the auto-login Xfce session
        env: {
          OBSIDIAN_BINARY: "/home/debian/.local/bin/obsidian", // not on PATH in the guest
          PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD: "1",
          ...(RUN_NET ? { RUN_NET_TESTS: "1" } : {}), // ATHENA_PYTHON persists from setup's export (posix)
        },
        ...(RUN_NET ? { setup: NET_SETUP_POSIX, teardownCmd: NET_TEARDOWN_POSIX } : {}),
        driverInstall: "npm install",  // into the per-runId workspace; cleaned by rm -rf
        runCmd: "node argus/src/host/vm-run-side.js",
        killOnCleanup: ["obsidian", "electron", "chrome", "chromium"],
        bootTimeoutMs: 180000,
      },
    },
    "windows-vm": {
      provision: {
        provider: "utm", vm: "Windows", teardown: "restore",
        transport: "ssh",
        ssh: { user: "debian", host: "auto" },   // host:auto → utmctl ip-address (192.168.64.4)
        // no display — Windows Chromium/CDP is headless (visible-desktop out of scope)
        env: {
          OBSIDIAN_BINARY: "C:\\Program Files\\Obsidian\\Obsidian.exe",
          PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD: "1",
          ...(RUN_NET ? {
            RUN_NET_TESTS: "1",
            // cmd /c setup loses its vars → pin ATHENA_PYTHON. Fixed C:\Users\Public
            // path (NOT $env:USERPROFILE): the guest profile dir is C:\Users\Tony,
            // not the SSH login `debian`, and env values can't expand at runtime.
            ATHENA_PYTHON: "C:\\Users\\Public\\athena-venv\\Scripts\\python.exe",
          } : {}),
        },
        ...(RUN_NET ? { setup: NET_SETUP_WIN, setupShell: "powershell", teardownCmd: NET_TEARDOWN_WIN } : {}),
        driverInstall: "npm install",  // transport runs it via `cmd /c`; cleaned with the workspace
        runCmd: "node argus/src/host/vm-run-side.js",
        killOnCleanup: ["obsidian", "electron", "chrome", "chromium"],
        bootTimeoutMs: 180000,
      },
    },
  },

  // ── Bundle (FLAT layout; dests = bundle-root-relative) ────────────
  bundle: {
    out: "/tmp/argus-bundle-athena.tar.gz",
    include: [
      // Harness wiring at bundle root (root = baseDir the runner resolves).
      { src: __filename, dest: "argus.config.js" },
      { src: path.join(__dirname, "playwright.config.js"), dest: "playwright.config.js" },
      { src: path.join(__dirname, "specs"), dest: "specs/" },
      // VM package manifest (@shared/argus → file:argus) → installed by
      // driverInstall's `npm install`.
      { src: path.join(__dirname, "package.vm.json"), dest: "package.json" },
      // Built plugins — dests MUST match the `resources` relPaths above.
      { src: HOST_ATHENA_PLUGIN, dest: ".obsidian/plugins/athena/" },
      { src: HOST_GRYPHON_PLUGIN, dest: ".obsidian/plugins/gryphon/" },
      // Python kb pipeline (specs spawn bin/kb).
      { src: path.join(ATHENA_ROOT, "bin"), dest: "bin/" },
      // Gryphon's protect package — 07-guardrail requires its attack-detector
      // + obsidian test-stub directly.
      { src: path.join(ATHENA_ROOT, "vendor", "gryphon", "packages", "protect"), dest: "vendor/gryphon/packages/protect/" },
      // The argus harness itself (shipped runner + @shared/argus resolution).
      { src: ARGUS_REPO, dest: "argus/" },
    ],
  },

  server: {
    port: 8000,
    resultsDir: "/tmp/argus-results",
  },
};
