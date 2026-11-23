"use strict";
// Wiki synthesis asks Gryphon for schema-validated JSON (#181) instead of
// regex-parsing model text; the text parse stays only as the soft landing
// when structured output gives up. Same fake-vendor technique as
// test-synthesis-teardown.js (synthesis.js requires a .ts path).
const { test } = require("node:test");
const assert = require("node:assert/strict");
const path = require("path");
const fs = require("fs");
const Module = require("module");

const SYNTHESIS_JS = path.resolve(__dirname, "../src/athena/synthesis.js");
const VENDOR = "../../vendor/gryphon/packages/provider-runtime/src/index";
const FAKE = "__athena_test_fake_vendor_structured__";

const _resolve = Module._resolveFilename.bind(Module);
Module._resolveFilename = function (request, parent, ...rest) {
  if (request === VENDOR && parent && parent.filename === SYNTHESIS_JS) return FAKE;
  return _resolve(request, parent, ...rest);
};

function load(send) {
  delete Module._cache[SYNTHESIS_JS];
  const m = new Module(FAKE);
  m.loaded = true;
  const calls = [];
  m.exports = {
    createProvider: () => ({
      onMessage: null, onError: null, abort() {},
      send: async (prompt, opts) => { calls.push(opts); return send(prompt, opts); },
    }),
    explainUnavailable: () => "unavailable",
  };
  Module._cache[FAKE] = m;
  return { syn: require(SYNTHESIS_JS), calls };
}

const plugin = { app: { vault: { adapter: { basePath: "/v" } } }, settings: {} };
const DIGEST = { summary: "A summary long enough to read as one paragraph.", body: "## Key Findings\n- x" };

class CliStructuredOutputError extends Error {
  constructor(msg, { reason, attempts, lastOutput }) {
    super(msg); this.name = "CliStructuredOutputError";
    this.reason = reason; this.attempts = attempts; this.lastOutput = lastOutput;
  }
}

test("callProvider requests the digest schema and returns result.json", async () => {
  const { syn, calls } = load(async () => ({ text: JSON.stringify(DIGEST), json: DIGEST, cost: 0.01 }));
  const r = await syn.callProvider(plugin, "p");
  assert.equal(r.ok, true);
  assert.deepEqual(r.json, DIGEST);
  assert.equal(calls[0].structuredOutput.name, "wiki_digest");
  assert.deepEqual(calls[0].structuredOutput.schema, syn.DIGEST_SCHEMA);
  assert.deepEqual(syn.DIGEST_SCHEMA.required, ["summary", "body"]);
});

test("structured output giving up falls back to the text parse of its last output", async () => {
  const last = "```json\n" + JSON.stringify(DIGEST) + "\n```";
  const orig = console.warn; const warned = []; console.warn = (...a) => warned.push(a);
  try {
    const { syn } = load(async () => { throw new CliStructuredOutputError("gave up", { reason: "validate", attempts: 3, lastOutput: last }); });
    const r = await syn.callProvider(plugin, "p");
    assert.equal(r.ok, true);
    assert.equal(r.json, undefined);
    assert.equal(r.structuredFailed, "validate");
    assert.deepEqual(syn.parseDigestResponse(r.text), { ok: true, summary: DIGEST.summary, body: DIGEST.body });
  } finally { console.warn = orig; }
  assert.equal(warned.length, 1, "the failure code and raw output are logged");
});

test("a structured failure with no output, or another error, is a provider error with its reason", async () => {
  for (const err of [
    new CliStructuredOutputError("gave up", { reason: "parse", attempts: 3, lastOutput: "" }),
    Object.assign(new Error("model returned end_turn"), { code: "STRUCTURED_OUTPUT_COERCION_FAILED" }),
  ]) {
    const { syn } = load(async () => { throw err; });
    const r = await syn.callProvider(plugin, "p");
    assert.equal(r.ok, false);
    assert.equal(r.reason, "provider-error");
    assert.equal(r.detail, err.message);
  }
});

test("digestFromObject normalises like the text parse and refuses non-objects", () => {
  const { syn } = load(async () => ({}));
  assert.deepEqual(syn.digestFromObject({ summary: ' "Summary: one\nparagraph" ', body: " b " }),
    { ok: true, summary: "one paragraph", body: "b" });
  for (const bad of [null, [], "x", 3]) assert.equal(syn.digestFromObject(bad).ok, false);
  assert.deepEqual(syn.digestFromObject({ summary: 7, body: null }), { ok: true, summary: "", body: "" });
});

test("synthesizeWikiPage takes result.json when present, the text parse otherwise", () => {
  const src = fs.readFileSync(SYNTHESIS_JS, "utf8");
  assert.match(src, /llmResult\.json !== undefined\s*\?\s*digestFromObject\(llmResult\.json\)\s*:\s*parseDigestResponse\(llmResult\.text\)/);
});
