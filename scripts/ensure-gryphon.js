// ensure-gryphon.js — make `npm run build` work in a clean clone.
//
// Athena's bundle imports Gryphon SOURCE from vendor/gryphon/ (see build.js).
// A bare `git clone` leaves that submodule empty, so esbuild cannot resolve
// the imports and `npm run build` fails — which is exactly what the Obsidian
// plugin review's clean-environment build check reports.
//
// This script bootstraps the submodule when it is missing: it initializes it
// from the pin recorded in .gitmodules (→ polleoai/gryphon) and installs the
// vendored workspace's dependencies (provider-runtime pulls in @google/genai,
// openai and undici, which esbuild resolves from vendor/gryphon/node_modules).
//
// It is a NO-OP when the submodule is already populated — CI checks out with
// `submodules: recursive`, and `build:all` inits it explicitly — so it adds no
// cost to the normal flows. It runs as the first step of the `build` script so
// it fires whenever a reviewer invokes `npm run build`, independent of whether
// install-time lifecycle scripts ran.
const fs = require("fs");
const { execSync } = require("child_process");

const SENTINEL = "vendor/gryphon/src/chat-view.js"; // a source file Athena imports
const VENDOR_NODE_MODULES = "vendor/gryphon/node_modules";

// All commands below are fixed string literals — no user/dynamic input is ever
// interpolated — so there is no shell-injection surface. (build.js uses execSync
// the same way.)
function sh(cmd) {
  execSync(cmd, { stdio: "inherit" });
}

if (!fs.existsSync(SENTINEL)) {
  console.log("[ensure-gryphon] vendor/gryphon is empty — initializing submodule…");
  try {
    sh("git submodule update --init --recursive vendor/gryphon");
  } catch {
    // fall through to the explicit error below
  }
}

if (!fs.existsSync(SENTINEL)) {
  console.error(
    "\n[ensure-gryphon] vendor/gryphon could not be initialized.\n" +
    "  Athena bundles Gryphon's source from this submodule, so a clean build\n" +
    "  needs it present. If you built from a source archive with no git\n" +
    "  metadata, clone with git instead:\n" +
    "    git clone --recurse-submodules https://github.com/polleoai/athena\n"
  );
  process.exit(1);
}

if (!fs.existsSync(VENDOR_NODE_MODULES)) {
  console.log("[ensure-gryphon] installing vendor/gryphon dependencies…");
  sh("npm --prefix vendor/gryphon install --no-audit --no-fund --silent");
}
