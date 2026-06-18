"use strict";

/**
 * 05-kb-add-url-matrix — AT-4/5/6 from docs/requirements-athena.md.
 *
 * Validates `kb add <url>` for each source-type dispatcher end-to-end:
 *   - YouTube   → arcus_video → raw/videos/artifacts/video-<id>.md
 *                 → wiki/format/videos/<title>.md
 *   - GitHub    → kb-capture repo branch → raw/repos/artifacts/...
 *   - Webpage   → arcus_html → raw/webpages/artifacts/...
 *
 * Real-network mode — gated by RUN_NET_TESTS=1 to keep the default
 * suite hermetic. Matches the pattern arcus itself uses for its own
 * smoke tests (test_fetch_metadata_smoke_real_network). Mocked
 * variants land in a follow-up commit once the arcus mock pattern
 * (argus AF5) crystallizes.
 *
 * P3 milestone (issue #185). Run with:
 *   RUN_NET_TESTS=1 npm run test:host
 */

const fs = require("fs");
const path = require("path");
const { execFileSync } = require("child_process");
const { test, expect, prepareKbVault, runKbCommand } = require("./_helpers");

const NETWORK_ENABLED = !!process.env.RUN_NET_TESTS;

// AT-5 (github) ingests via kb-capture's `gh` CLI branch (`gh api repos/.../readme`),
// which needs the GitHub CLI installed AND authenticated. That holds on a dev host
// but not in a throwaway VM guest (no token), so AT-5 self-skips where gh isn't
// usable — the github path stays covered by the host run. AT-4/6 need no auth.
// execFileSync (no shell) with a fixed arg array — nothing interpolated.
const GH_AVAILABLE = (() => {
  try {
    execFileSync("gh", ["auth", "status"], { stdio: "ignore" });
    return true;
  } catch {
    return false;
  }
})();

test.describe("AT-4/5/6: kb add <url> matrix (real-network, gated)", () => {
  test.skip(
    !NETWORK_ENABLED,
    "real-network tests skipped — set RUN_NET_TESTS=1 to enable. " +
      "Mocked variants pending argus AF5.",
  );

  test.beforeEach(async ({ obsidian }) => {
    await prepareKbVault(obsidian.vaultPath);
  });

  // ─── AT-4: YouTube ──────────────────────────────────────────────
  // "Me at the zoo" — the first YouTube video ever, posted 2005.
  // Stable for two decades, has English auto-captions, and is in
  // arcus's own real-network smoke test. The video ID `jNQXAC9IVRw`
  // is what the C-fix slug logic must produce as `video-jNQXAC9IVRw`.
  //
  // KNOWN EXTERNAL DEPENDENCY (not a regression): on a host without a JS
  // runtime (deno/node) usable by yt-dlp, yt-dlp >= ~2026.3.x silently returns
  // NO captions -> arcus emits an empty transcript -> no video wiki page ->
  // this test's `wikiFiles.length > 0` assertion fails. The macOS host passes
  // only because it has an incidental Homebrew `deno`; a fresh Linux/Windows
  // guest with `node` present but no deno fails, because yt-dlp auto-enables
  // only deno. The fix lives in arcus (detect+use the `node` already on PATH):
  // see jivebug/arcus docs/requirements-youtube-transcript-js-runtime.md
  // (commit 70641b2). A red HERE means "arcus JS-runtime fix not yet shipped,"
  // NOT a kb/kb-capture port regression. Goes green automatically once arcus
  // R1 lands and the guest's existing node is used — no harness change needed.
  test("AT-4: kb add youtube URL writes video-<id> slug + clean summary", async ({ obsidian }) => {
    const url = "https://www.youtube.com/watch?v=jNQXAC9IVRw";
    const result = await runKbCommand(obsidian.page, "add", [url]);

    expect(
      result.ok,
      `kb add ${url} failed:\nstdout:\n${result.stdout}\nstderr:\n${result.stderr}`,
    ).toBe(true);

    // C fix: the slug derivation produces `video-<vid_id>` for any
    // youtube watch URL. raw_writer may lowercase the ID; allow both.
    const rawDir = path.join(obsidian.vaultPath, "raw", "videos", "artifacts");
    const rawFiles = fs.readdirSync(rawDir);
    const expectSlugs = ["video-jNQXAC9IVRw.md", "video-jnqxac9ivrw.md"];
    const hasCorrectSlug = rawFiles.some((f) => expectSlugs.includes(f));
    expect(
      hasCorrectSlug,
      `expected one of ${expectSlugs.join(" / ")} in ${rawDir}; got: ${rawFiles.join(", ")}`,
    ).toBe(true);

    // The OLD (pre-C-fix) buggy slug must NOT be on disk — that's
    // the collision-prone form every YouTube URL used to collapse
    // to.
    expect(rawFiles).not.toContain("youtube-com-watch.md");

    // Wiki page exists pointing at the correct raw.
    const wikiDir = path.join(obsidian.vaultPath, "wiki", "format", "videos");
    const wikiFiles = fs.readdirSync(wikiDir).filter((f) => f.endsWith(".md") && !f.startsWith("_"));
    expect(wikiFiles.length).toBeGreaterThan(0);

    // B / D fix: no commenter handles in the page (whichever path
    // produced the summary, it must not have leaked comment-tree
    // chrome from the Web Clipper-style capture).
    for (const f of wikiFiles) {
      const content = fs.readFileSync(path.join(wikiDir, f), "utf8");
      expect(
        content,
        `commenter-style content leaked into ${f}`,
      ).not.toMatch(/@[A-Za-z0-9_-]+ \w/);  // @handle followed by text
      expect(content).not.toMatch(/Top comment|pinned comment|viewer comment/i);
    }
  });

  // ─── AT-5: GitHub repo ──────────────────────────────────────────
  // octocat/Hello-World — GitHub's universally stable public test
  // repo. Tiny (one README line), so the capture cost is minimal.
  // Slug scheme is `<owner>--<repo>` (the gh CLI form), per
  // bin/lib/slug.py's URL_DERIVED policy for the "repos" category.
  test("AT-5: kb add github URL writes a repo raw + wiki", async ({ obsidian }) => {
    test.skip(
      !GH_AVAILABLE,
      "github ingest needs an authenticated `gh` CLI (absent in throwaway guests) — " +
        "covered by the host run",
    );
    const url = "https://github.com/octocat/Hello-World";
    const result = await runKbCommand(obsidian.page, "add", [url]);

    expect(
      result.ok,
      `kb add ${url} failed:\nstdout:\n${result.stdout}\nstderr:\n${result.stderr}`,
    ).toBe(true);

    const rawDir = path.join(obsidian.vaultPath, "raw", "repos", "artifacts");
    expect(fs.existsSync(rawDir)).toBe(true);
    const rawFiles = fs.readdirSync(rawDir).filter((f) => f.endsWith(".md"));
    expect(
      rawFiles.length,
      `expected at least one raw in ${rawDir}; got: ${rawFiles.join(", ")}.\nkb add output:\n${result.stdout}`,
    ).toBeGreaterThan(0);

    // At least one raw mentions the input owner/repo (frontmatter
    // source: or body). Robust to slug variants (octocat--Hello-World
    // vs github-com-octocat-hello-world).
    const hasMatchingRaw = rawFiles.some((f) => {
      const content = fs.readFileSync(path.join(rawDir, f), "utf8").slice(0, 1500);
      return content.toLowerCase().includes("octocat")
        && content.toLowerCase().includes("hello-world");
    });
    expect(hasMatchingRaw, `no raw mentions octocat/Hello-World: ${rawFiles.join(", ")}`).toBe(true);
  });

  // ─── AT-6: Generic webpage ─────────────────────────────────────
  // example.com — stable, no auth, no JS rendering needed. Exercises
  // the arcus_html dispatcher (same path P2/AT-7 exercised via the
  // inbox flow; this one is the direct-URL variant).
  test("AT-6: kb add webpage URL writes raw + wiki", async ({ obsidian }) => {
    const url = "https://example.com/";
    const result = await runKbCommand(obsidian.page, "add", [url]);

    expect(
      result.ok,
      `kb add ${url} failed:\nstdout:\n${result.stdout}\nstderr:\n${result.stderr}`,
    ).toBe(true);

    const rawDir = path.join(obsidian.vaultPath, "raw", "webpages", "artifacts");
    expect(fs.existsSync(rawDir)).toBe(true);
    const rawFiles = fs.readdirSync(rawDir).filter((f) => f.endsWith(".md"));
    expect(rawFiles.length).toBeGreaterThan(0);

    const wikiDir = path.join(obsidian.vaultPath, "wiki", "format", "webpages");
    const wikiFiles = fs.readdirSync(wikiDir).filter((f) => f.endsWith(".md") && !f.startsWith("_"));
    expect(wikiFiles.length).toBeGreaterThan(0);
  });
});
