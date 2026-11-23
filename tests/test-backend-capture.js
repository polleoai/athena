/**
 * test-backend-capture.js — node --test
 *
 * Witnessed 2026-11-22: `kb add https://arxiv.org/pdf/2611.20218` in the
 * Obsidian chat said "No content captured." although the capture worked.
 * The plugin's browser capture can't read a PDF, so it fell back to the
 * Python `kb add`, which saved raw/papers/artifacts/arxiv-2611-20218.md AND
 * created the wiki page. The plugin then re-read the raw under the slug IT
 * had guessed from the URL, found nothing, and reported failure.
 *
 * The rules (src/athena/backend-capture.js):
 *   1. savedRawPath reads the raw path from the backend's own "Saved:" line
 *      (vault-relative, also when printed absolute), never a guess.
 *   2. wikiPageForRaw finds the wiki page that lists that raw, by raw_path
 *      or in raw_paths, and returns its name and summary; null if none.
 *   3. plugin.js uses them in the fallback: a page the backend made is
 *      reported as created, not re-built or called a failure.
 */
"use strict";

const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("fs");
const os = require("os");
const path = require("path");

const SRC = path.join(__dirname, "..", "src", "athena");
const { savedRawPath, wikiPageForRaw } = require(path.join(SRC, "backend-capture.js"));

const OUT = [
  "Capturing: https://arxiv.org/pdf/2611.20218",
  "  Detected: paper",
  "Saved: raw/papers/artifacts/arxiv-2611-20218.md",
  "── Creating wiki page: arxiv-2611-20218",
].join("\n");

test("savedRawPath reads the backend's Saved: line, indented or absolute", () => {
  assert.equal(savedRawPath(OUT, "/v"), "raw/papers/artifacts/arxiv-2611-20218.md");
  assert.equal(savedRawPath("  Saved: raw/webpages/artifacts/a.md\n", "/v"), "raw/webpages/artifacts/a.md");
  assert.equal(savedRawPath("Saved: /v/raw/repos/artifacts/b.md\n", "/v"), "raw/repos/artifacts/b.md");
  assert.equal(savedRawPath("Saved: raw/x/one.md\nSaved: raw/x/two.md\n", "/v"), "raw/x/two.md",
    "the last Saved: line is the source that was just written");
  assert.equal(savedRawPath("Error: nothing here\n", "/v"), null);
});

test("savedRawPath only accepts a .md inside the vault's raw/ (output can echo page text)", () => {
  for (const bad of [
    "Saved: ../../.ssh/id_rsa",
    "Saved: raw/../../etc/passwd.md",
    "Saved: /etc/hosts",
    "Saved: /v/../outside/raw/x.md",
    "Saved: wiki/format/papers/x.md",
    "Saved: raw/papers/artifacts/x.pdf",
  ]) {
    assert.equal(savedRawPath(bad + "\n", "/v"), null, bad);
  }
  assert.equal(savedRawPath("Saved: /v/raw/papers/artifacts/ok.md\n", "/v"), "raw/papers/artifacts/ok.md");
  assert.equal(savedRawPath("Saved: raw/papers/artifacts/ok.md\nSaved: ../../x.md\n", "/v"),
    "raw/papers/artifacts/ok.md", "an unsafe later line does not hide the real one");
});

function vault() {
  const v = fs.mkdtempSync(path.join(os.tmpdir(), "athena-bc-"));
  fs.mkdirSync(path.join(v, "wiki/format/papers"), { recursive: true });
  fs.mkdirSync(path.join(v, "wiki/format/webpages"), { recursive: true });
  return v;
}

test("wikiPageForRaw finds the page by raw_path and returns its summary", () => {
  const v = vault();
  fs.writeFileSync(path.join(v, "wiki/format/papers/arXiv: SWE-CoWork.md"),
    '---\ntitle: "arXiv: SWE-CoWork"\nraw_path: "raw/papers/artifacts/arxiv-2611-20218.md"\n' +
    'summary: "A benchmark for coding agents."\n---\nbody\n');
  fs.writeFileSync(path.join(v, "wiki/format/papers/Other.md"),
    '---\ntitle: "Other"\nraw_path: "raw/papers/artifacts/other.md"\nsummary: "no"\n---\n');
  assert.deepEqual(wikiPageForRaw(v, "raw/papers/artifacts/arxiv-2611-20218.md"),
    { pageName: "arXiv: SWE-CoWork", summary: "A benchmark for coding agents." });
});

test("wikiPageForRaw finds a merged page listing the raw in raw_paths", () => {
  const v = vault();
  fs.writeFileSync(path.join(v, "wiki/format/webpages/Merged.md"),
    '---\ntitle: "Merged"\nraw_paths:\n  - "raw/webpages/artifacts/a.md"\n  - "raw/webpages/artifacts/b.md"\n---\n');
  assert.equal(wikiPageForRaw(v, "raw/webpages/artifacts/b.md").pageName, "Merged");
});

test("wikiPageForRaw: no page for the raw is null, and a body mention does not count", () => {
  const v = vault();
  fs.writeFileSync(path.join(v, "wiki/format/webpages/Mentions.md"),
    '---\ntitle: "Mentions"\nraw_path: "raw/webpages/artifacts/z.md"\n---\nsee raw/webpages/artifacts/a.md\n');
  assert.equal(wikiPageForRaw(v, "raw/webpages/artifacts/a.md"), null);
  assert.equal(wikiPageForRaw(v, null), null);
});

test("plugin.js reports the backend's page instead of re-reading a guessed slug", () => {
  const src = fs.readFileSync(path.join(SRC, "plugin.js"), "utf8");
  assert.match(src, /require\("\.\/backend-capture"\)/);
  const i = src.indexOf("Webview capture failed, trying Python backend");
  assert.ok(i > 0, "the fallback branch is where it was");
  const branch = src.slice(i, i + 2500);
  assert.match(branch, /savedRawPath\(captureOutput, vaultPath\)/, "reads the real saved path");
  assert.match(branch, /wikiPageForRaw\(vaultPath, /, "looks for the page the backend made");
  assert.match(branch, /status: "created"/, "and reports it as created");
});
