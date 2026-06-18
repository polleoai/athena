"use strict";

/**
 * Playwright config for Athena's host-only smoke run.
 * Mirrors gryphon's shape — see ~/Projects/gryphon/scripts/vm-tests/playwright.config.js.
 */

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
