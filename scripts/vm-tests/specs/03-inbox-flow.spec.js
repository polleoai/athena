"use strict";

/**
 * 03-inbox-flow — AT-7 from docs/requirements-athena.md.
 *
 * Validates the inbox-processing flow end-to-end:
 *   1. Seed a clip file into `<vault>/inbox/Clippings/<name>.md`
 *   2. Invoke `kb add` (no args) — the inbox-processing dispatcher
 *      walks Clippings/ and processes each one through process_clip
 *   3. Assert: the clip is moved out of Clippings/ (into .processed/
 *      or trashed if it failed), AND if it succeeded, a wiki page
 *      exists for it
 *
 * The clip used is a deliberately-minimal Web-Clipper-shape capture
 * of example.com — a stable target that doesn't depend on the live
 * web (the clip body is bundled, not fetched).
 *
 * P2 milestone (issue #184).
 */

const fs = require("fs");
const path = require("path");
const { test, expect, prepareKbVault, runKbCommand } = require("./_helpers");

const SAMPLE_CLIP = `---
title: "Example Domain"
source: "https://example.com/"
captured_at: "2026-06-01T00:00:00.000Z"
clipped_via: "browser-capture"
---

# Example Domain

This domain is for use in illustrative examples in documents. You may
use this domain in literature without prior coordination or asking for
permission.

[More information…](https://www.iana.org/domains/example)
`;

test.describe("AT-7: Web Clipper inbox → kb add → wiki page", () => {
  test.beforeEach(async ({ obsidian }) => {
    await prepareKbVault(obsidian.vaultPath);
  });

  test("clip in inbox/Clippings/ is processed by `kb add` (no args)", async ({ obsidian }) => {
    // Stage the clip after fixture init — AF3 seedFiles would also
    // work, but doing it here keeps the clip content next to the
    // assertion that consumes it.
    const clipPath = path.join(obsidian.vaultPath, "inbox", "Clippings", "Example Domain.md");
    await fs.promises.writeFile(clipPath, SAMPLE_CLIP);

    // Sanity: clip is where we expect before kb add runs.
    expect(fs.existsSync(clipPath)).toBe(true);

    // The kb add (no args) inbox dispatcher walks Clippings/.
    const result = await runKbCommand(obsidian.page, "add", []);
    expect(
      result.ok,
      `kb add failed:\nstdout:\n${result.stdout}\nstderr:\n${result.stderr}`,
    ).toBe(true);

    // The clip should be moved out of Clippings/ — either into
    // .processed/ on success, or .quarantined/ on failure. Either
    // way, NOT in Clippings/ root any more.
    expect(fs.existsSync(clipPath)).toBe(false);

    // Wiki page for example.com should now exist in webpages/
    // (canonical slug derived from URL). The exact slug depends on
    // url canonicalization — match any .md under webpages/ that
    // mentions example.com in its first 500 bytes.
    const webpagesDir = path.join(obsidian.vaultPath, "wiki", "format", "webpages");
    const wikiFiles = fs.existsSync(webpagesDir) ? fs.readdirSync(webpagesDir) : [];
    const hasExample = wikiFiles.some((f) => {
      if (!f.endsWith(".md") || f.startsWith("_")) return false;
      const content = fs.readFileSync(path.join(webpagesDir, f), "utf8").slice(0, 500);
      return content.toLowerCase().includes("example.com")
        || content.toLowerCase().includes("example domain");
    });
    expect(
      hasExample,
      `no wiki page mentioning example.com found in ${webpagesDir}. ` +
        `Files: ${wikiFiles.join(", ")}. Output:\n${result.stdout}`,
    ).toBe(true);
  });
});
