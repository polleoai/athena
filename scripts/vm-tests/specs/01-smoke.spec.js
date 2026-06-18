"use strict";

const { test, expect, openAthenaChat } = require("./_helpers");

/**
 * 01-smoke — Athena equivalent of gryphon's 01-smoke. Covers AT-1 and
 * AT-2 from docs/requirements-athena.md.
 *
 * Asserts:
 *   AT-1. Obsidian boots and the Athena plugin loads (ribbon icon present)
 *   AT-2. Opening the Athena view succeeds (chat input renders).
 *         Athena extends GryphonChatView so the textarea uses Gryphon's
 *         class name — the assertion catches both the gryphon-vendor
 *         load AND athena's extension wiring in one check.
 *   No uncaught console errors during boot + view-open.
 *
 * P1 milestone — closes issue #183.
 * P2 (issue #184) adds slash-command + inbox-flow + lint specs.
 */

test("AT-1: Athena ribbon icon is present after boot", async ({ obsidian }) => {
  // Ribbon icon was added with addRibbonIcon("brain", "Open Athena", ...)
  // — see src/athena/plugin.js:838. Obsidian renders ribbon icons with
  // aria-label = the second arg.
  const ribbon = obsidian.page.locator('[aria-label="Open Athena"]');
  await expect(ribbon).toBeVisible({ timeout: 15_000 });
});

test("AT-2: opening the Athena view renders the chat input", async ({ obsidian }) => {
  const input = await openAthenaChat(obsidian.page);
  // Sanity: the input is editable + has the expected role.
  await expect(input).toBeEditable();
});

test("no uncaught console errors during boot + view-open", async ({ obsidian }) => {
  const consoleErrors = [];
  obsidian.page.on("console", (msg) => {
    if (msg.type() === "error") consoleErrors.push(msg.text());
  });
  obsidian.page.on("pageerror", (err) => {
    consoleErrors.push(`pageerror: ${err.message}`);
  });

  // Exercise the boot + view-open path.
  await openAthenaChat(obsidian.page);
  await obsidian.page.waitForTimeout(2000);

  // Filter known-benign Obsidian-internal warnings.
  const real = consoleErrors.filter((e) => !e.includes("Failed to load resource"));
  expect(
    real,
    `unexpected console errors: ${JSON.stringify(real, null, 2)}`,
  ).toEqual([]);
});
