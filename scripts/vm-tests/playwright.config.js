"use strict";

/**
 * Playwright config for Athena's host-only smoke run.
 * Mirrors gryphon's shape — see ~/Projects/gryphon/scripts/vm-tests/playwright.config.js.
 */

// The user's rule (2026-11-16): Athena's GUI/VM runs happen on the Pro, never
// the Max, so `npx playwright test` is refused there too, not only via npm.
// Inside a VM the bundle has no scripts/lib/, and a VM isn't the Max.
const _proOnly = require("path").join(__dirname, "..", "lib", "pro-only.js");
if (require("fs").existsSync(_proOnly)) require(_proOnly).refuseOnMax("vm-tests playwright");

module.exports = {
  testDir: "./specs",
  // Argus's obsidian fixture (in _helpers.js) launches Obsidian per-test;
  // running serially avoids two specs racing on the same vault tmp dir.
  fullyParallel: false,
  workers: 1,
  retries: 0,
  // Specs cold-launch Electron; allow generous per-test budget.
  timeout: 90_000,
  reporter: [
    ["list"],
    ["json", { outputFile: "test-results/results.json" }],
  ],
};
