"use strict";
// Long X posts the syndication CDN serves only as a ~280-char preview
// (techNmak/status/2122711743369785617, 2026-11-17). The plugin keeps the
// marked preview unless its browser capture really holds more of the post.
// The fixture is fetch_tweet.build_raw's own output (tests/fixtures/
// x-long-post.json), so the JS and Python sides read the same raw.
const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("fs");
const path = require("path");
const { previewFromRaw, isFullerCapture, classifyTweetFetch } = require("../src/athena/tweet-capture");

const fx = JSON.parse(fs.readFileSync(path.join(__dirname, "fixtures", "x-long-post.json"), "utf8"));

test("the preview is read out of the raw: no frontmatter, byline, note or media", () => {
  const p = previewFromRaw(fx.floor);
  assert.match(p, /^These are literally the best visual resources/);
  assert.match(p, /2\/ Brendan$/);
  assert.doesNotMatch(p, /Incomplete capture|Tech with Mak|capture_incomplete|t\.co/);
});

test("a browser capture of the full post is fuller", () => {
  assert.equal(isFullerCapture("Tech with Mak @techNmak\n\n" + fx.full, fx.floor), true);
});

test("X's page rendering, with a shortened display URL mid-text, is fuller", () => {
  const page = "Tech with Mak @techNmak\n" + fx.full.replace("https://t.co/abc", "poloclub.github.io/transformer-ex\u2026")
    + "\n10:03 AM \u00b7 Nov 16, 2026 \u00b7 12K Views\nLog in Sign up";
  assert.equal(isFullerCapture(page, fx.floor), true);
});

test("the real X page, display URLs split over lines, is fuller", () => {
  const page = "Tech with Mak\n@techNmak\nThese are literally the best visual resources for learning how " +
    "Transformers, LLMs, embeddings, diffusion, and inference work \u2193\n\n1/ Transformer Explainer - Watch GPT " +
    "process text through embeddings, attention, MLPs and next-token prediction.\n\nhttps://\n" +
    "poloclub.github.io/transformer-ex\nplainer/\n\u2026\n\n2/ Brendan Bycroft\u2019s LLM Visualization - " +
    "Explore an LLM from architecture down to tensors and operations.\n\nhttps://\nbbycroft.net/llm\n";
  assert.equal(isFullerCapture(page, fx.floor), true);
  assert.equal(isFullerCapture(page.split("2/ Brendan")[0] + "2/ Brendan\u2026 Show more", fx.floor), false);
});

test("preview, then an image and a quoted post, is not fuller", () => {
  const page = previewFromRaw(fx.floor) + "\n\n![Image](../../assets/x/1.jpg)\n\n---\n\n**Quoting Someone @else:**\n\n> A different post, long enough.";
  assert.equal(isFullerCapture(page, fx.floor), false);
});

test("a login wall, the preview itself, or nothing is not", () => {
  assert.equal(isFullerCapture("Log in to X. Don't miss what's happening. ".repeat(30), fx.floor), false);
  assert.equal(isFullerCapture(fx.preview, fx.floor), false);
  assert.equal(isFullerCapture("", fx.floor), false);
  assert.equal(isFullerCapture(undefined, fx.floor), false);
});

test("the preview wrapped in page chrome is not fuller", () => {
  assert.equal(isFullerCapture(fx.floor, fx.floor), false);
  const head = previewFromRaw(fx.floor);
  for (const after of ["\u2026 Show more", " 10:03 AM \u00b7 Nov 16, 2026", " <img src=x>"]) {
    assert.equal(isFullerCapture("Tech with Mak\n" + head + after + " Log in Sign up".repeat(5), fx.floor), false, after);
  }
});

test("fetch_tweet exit codes: 0 complete, 3 incomplete floor, others fail", () => {
  assert.deepEqual(classifyTweetFetch(0, fx.floor), { raw: fx.floor.trim(), incomplete: false });
  assert.deepEqual(classifyTweetFetch(3, fx.floor), { raw: fx.floor.trim(), incomplete: true });
  assert.equal(classifyTweetFetch(1, fx.floor), null);
  assert.equal(classifyTweetFetch(3, "short"), null);
});

test("plugin.js wiring: floor kept unless fuller, browser long-post raws marked", () => {
  const src = fs.readFileSync(path.join(__dirname, "..", "src", "athena", "plugin.js"), "utf8");
  assert.match(src, /classifyTweetFetch\(err \? err\.code : 0, stdout\)/);
  assert.match(src, /if \(tweetFloor && !\(browserResult && isFullerCapture\(browserResult\.text, tweetFloor\)\)\)/);
  assert.match(src, /capture_route: "x-long-post-browser"/);
  assert.match(src, /new Notice\("Athena: X sent only the opening of this long post/);
});
