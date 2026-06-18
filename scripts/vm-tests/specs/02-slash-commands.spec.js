"use strict";

/**
 * 02-slash-commands — AT-3 from docs/requirements-athena.md.
 *
 * Validates the kb→subprocess pipeline via athena's `runMechanical`
 * helper (the SAME path that fires when the user types `kb stats` /
 * `kb list` / `kb search` in the chat panel — see plugin.js:824
 * `onBeforeSend: text => this._handleKbCommand(text)`). Bypassing
 * the chat textarea makes the spec deterministic; the smoke spec
 * already covers that the chat input renders.
 *
 * P2 milestone (issue #184).
 */

const { test, expect, prepareKbVault, runKbCommand } = require("./_helpers");

test.describe("AT-3: kb slash commands round-trip through runMechanical", () => {
  // Per-test setup: stage the bin tree + raw/wiki/inbox skeleton into
  // the fresh test vault so `kb` can spawn against `<vault>/bin/kb`.
  test.beforeEach(async ({ obsidian }) => {
    await prepareKbVault(obsidian.vaultPath);
  });

  test("kb stats: exits 0 and reports the expected sections", async ({ obsidian }) => {
    const result = await runKbCommand(obsidian.page, "stats");
    expect(
      result.ok,
      `kb stats failed:\nstdout:\n${result.stdout}\nstderr:\n${result.stderr}`,
    ).toBe(true);
    // The stats command's output mentions counts for each category;
    // assert the section header that survives even on an empty vault.
    expect(result.stdout.toLowerCase()).toMatch(/raw|wiki|sources/);
  });

  test("kb list: returns a parseable response (no crash)", async ({ obsidian }) => {
    const result = await runKbCommand(obsidian.page, "list", []);
    // Empty vault → "No pages found." or similar; either way exit 0.
    expect(
      result.ok,
      `kb list failed:\nstdout:\n${result.stdout}\nstderr:\n${result.stderr}`,
    ).toBe(true);
  });

  test("kb list --recent: handles flag args correctly", async ({ obsidian }) => {
    const result = await runKbCommand(obsidian.page, "list", ["--recent"]);
    expect(
      result.ok,
      `kb list --recent failed:\nstdout:\n${result.stdout}\nstderr:\n${result.stderr}`,
    ).toBe(true);
  });

  test("kb search foo: returns no results on empty vault without crashing", async ({ obsidian }) => {
    const result = await runKbCommand(obsidian.page, "search", ["foo"]);
    expect(
      result.ok,
      `kb search failed:\nstdout:\n${result.stdout}\nstderr:\n${result.stderr}`,
    ).toBe(true);
    // Either no results, or 0-match — never a Python traceback.
    expect(result.stderr).not.toMatch(/Traceback/);
  });
});
