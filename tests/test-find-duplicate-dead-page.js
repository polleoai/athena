/**
 * test-find-duplicate-dead-page.js — node --test
 *
 * Regression guard for 2026-09-17: `kb add` on a live LinkedIn post replied
 * "Already in knowledge base: [[LinkedIn: LinkedIn 404 — page not found]] —
 * Matched by title (similar)". LinkedIn had served its error page, and
 * findDuplicate() scored "Page Not Found | LinkedIn" against an older
 * error-page capture by word overlap, so the real capture never ran.
 *
 * Invariant: an error-page title or body is never an identity. Two different
 * dead URLs must not be "duplicates" of each other, and an error-titled or
 * redirect-stub page must never claim a new capture by title or content.
 * Genuine title, content and URL duplicates must still be caught.
 *
 * plugin.js imports Obsidian, so the two methods are lifted out of the source
 * by brace-matching and evaluated with fs/path/resolvePythonScript in scope.
 * ATHENA_PLUGIN_SRC overrides the source file — run it against the pre-fix
 * plugin.js to confirm this test FAILS on the defect it guards:
 *   git show <rev>:src/athena/plugin.js > /tmp/old.js
 *   ATHENA_PLUGIN_SRC=/tmp/old.js node --test tests/test-find-duplicate-dead-page.js
 */
"use strict";

const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("fs");
const os = require("os");
const path = require("path");

const REPO = path.resolve(__dirname, "..");
const SRC = process.env.ATHENA_PLUGIN_SRC || path.join(REPO, "src/athena/plugin.js");

function liftMethod(src, signature) {
  const at = src.indexOf(`\n  ${signature} {`);
  if (at < 0) return null;
  let i = src.indexOf("{", at), depth = 0;
  for (let j = i; j < src.length; j++) {
    if (src[j] === "{") depth++;
    else if (src[j] === "}" && --depth === 0) return src.slice(at + 1, j + 1);
  }
  throw new Error(`unbalanced braces in ${signature}`);
}

function buildDeduper() {
  const src = fs.readFileSync(SRC, "utf8");
  const methods = ["deadPageLabel(text)", "findDuplicate(url, title, bodyLines)"]
    .map((sig) => liftMethod(src, sig)).filter(Boolean);
  // Same resolution order as the real resolvePythonScript: plugin dir, then vault root.
  const resolvePythonScript = (plugin, rel) => path.join(REPO, rel);
  const Klass = new Function("fs", "path", "resolvePythonScript",
    `return class { ${methods.join("\n")} }`)(fs, path, resolvePythonScript);
  return Klass;
}

function makeVault(pages) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "athena-dedup-"));
  const dir = path.join(root, "wiki/format/webpages");
  fs.mkdirSync(dir, { recursive: true });
  for (const [name, fm, body] of pages) {
    const lines = Object.entries(fm).map(([k, v]) => `${k}: ${typeof v === "string" ? `"${v}"` : v}`);
    fs.writeFileSync(path.join(dir, `${name}.md`), `---\n${lines.join("\n")}\n---\n\n${body || ""}\n`);
  }
  const Klass = buildDeduper();
  const d = new Klass();
  d.app = { vault: { adapter: { basePath: root } } };
  return d;
}

const ERROR_PAGE = ["LinkedIn: LinkedIn 404 — page not found",
  { title: "LinkedIn: LinkedIn 404 — page not found", url: "https://www.linkedin.com/feed/update/urn:li:activity:111" },
  "Page not found. Uh oh, we can't seem to find the page you're looking for. Try going back to the previous page."];

const REAL = ["LinkedIn: The 99% Accuracy Trap — Why Metrics Lie on Imbalanced Datasets",
  { title: "LinkedIn: The 99% Accuracy Trap — Why Metrics Lie on Imbalanced Datasets",
    url: "https://www.linkedin.com/feed/update/urn:li:activity:222" },
  "A model that predicts the majority class on an imbalanced dataset can report 99% accuracy while catching none of the cases you care about.\n\nPrecision, recall and PR-AUC are the metrics that expose it, because they ignore the true negatives that dominate the count."];

test("9/17 repro: an error-page title does not match an error-titled page", () => {
  const d = makeVault([ERROR_PAGE]);
  assert.equal(d.findDuplicate("https://www.linkedin.com/feed/update/urn:li:activity:7506400425349103616",
    "Page Not Found | LinkedIn", null), null);
});

test("two different dead URLs are not duplicates by error-page content", () => {
  const d = makeVault([ERROR_PAGE]);
  assert.equal(d.findDuplicate(null, "Page Not Found | LinkedIn", ERROR_PAGE[2].split("\n")), null);
});

test("an error-titled page cannot claim a capture by content", () => {
  const d = makeVault([["LinkedIn: LinkedIn 404 — page not found",
    { title: "LinkedIn: LinkedIn 404 — page not found" }, REAL[2]]]);
  assert.equal(d.findDuplicate(null, "Some unrelated real title here", REAL[2].split("\n")), null);
});

test("a redirect stub never matches by title", () => {
  const d = makeVault([["LinkedIn: Old Name For The Accuracy Trap Post",
    { title: "LinkedIn: Old Name For The Accuracy Trap Post", redirect: true,
      redirect_to: REAL[0] }, ""]]);
  assert.equal(d.findDuplicate(null, "Old Name For The Accuracy Trap Post", null), null);
});

test("genuine duplicates are still caught — URL, title, and content", () => {
  const d = makeVault([REAL]);
  assert.equal(d.findDuplicate("https://www.linkedin.com/feed/update/urn:li:activity:222", "x", null).method, "URL");
  assert.equal(d.findDuplicate(null, REAL[1].title, null).method, "title");
  assert.equal(d.findDuplicate(null, "Totally different words entirely here", REAL[2].split("\n")).method, "content");
});

test("a dead capture still matches an existing page by URL", () => {
  const d = makeVault([REAL]);
  const r = d.findDuplicate("https://www.linkedin.com/feed/update/urn:li:activity:222", "Page Not Found | LinkedIn", null);
  assert.equal(r && r.method, "URL");
});
