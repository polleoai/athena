"use strict";
// kb add's chat line for a failed synthesis shows the provider's reason.
// Gryphon 2.13.1 (#55): Codex refuses when its sandbox can't run, and a
// bare "provider-error … retry" would hide the one thing to change.
const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("fs");
const path = require("path");
const { synthesisFailureLine } = require("../src/athena/synthesis-message");

const SANDBOX = "Gryphon won't start Codex here: Codex's 'read-only' sandbox needs Linux 5.13 or newer " +
  "(landlock), and this computer runs Linux 5.10.0. Without it, Codex would run with full access " +
  "to your computer. Update to Linux 5.13 or newer, or, if you want Codex to have full access, " +
  "switch Gryphon's permission mode to YOLO.";

test("a provider error shows its reason, then the retry hint", () => {
  const line = synthesisFailureLine({ ok: false, reason: "provider-error", detail: SANDBOX }, "https://e.x/a");
  assert.match(line, /^\*Synthesis failed\* \(provider-error\): Gryphon won't start Codex here/);
  assert.match(line, /Linux 5\.13/);
  assert.match(line, /Run `kb regen https:\/\/e\.x\/a` to retry once that's fixed\.$/);
});

test("the reason is one line and capped", () => {
  const line = synthesisFailureLine({ ok: false, reason: "exception", detail: "a\n\nb\t" + "x".repeat(900) }, "u");
  assert.doesNotMatch(line, /\n/);
  assert.ok(line.length < 400, String(line.length));
});

test("stderr is the fallback reason; nothing at all keeps the old line", () => {
  assert.match(synthesisFailureLine({ ok: false, reason: "provider-error", stderr: "boom" }, "u"), /: boom Run/);
  assert.equal(synthesisFailureLine({ ok: false, reason: "provider-error" }, "u"),
    "*Synthesis failed* (provider-error). Run `kb regen u` to retry.");
});

test("no-provider and success are unchanged", () => {
  assert.match(synthesisFailureLine({ ok: false, reason: "no-provider" }, "u"), /^\*Synthesis skipped:\* no LLM provider/);
  assert.equal(synthesisFailureLine({ ok: true }, "u"), null);
  assert.equal(synthesisFailureLine(null, "u"), null);
});

test("plugin.js uses it for kb add's result", () => {
  const src = fs.readFileSync(path.join(__dirname, "..", "src", "athena", "plugin.js"), "utf8");
  assert.match(src, /synthesisFailureLine\(result\.synthesis, url\)/);
  assert.doesNotMatch(src, /\*Synthesis failed\* \(\$\{result\.synthesis\.reason\}\)\. Run/);
});
