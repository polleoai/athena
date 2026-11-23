"use strict";
// llmProcessContent's reply is schema-validated through Gryphon's CLI
// structured-output helpers (#181), retried on a bad reply, with the old
// regex parse only as the soft landing. Every case the regex era failed on
// historically is pinned here: a fenced reply, a trailing comma, a missing
// field, prose around the object.
const { test } = require("node:test");
const assert = require("node:assert/strict");
const { processWithSchema, PAGE_FIELDS_OUTPUT } = require("../src/athena/process-content");

const GOOD = { title: "X — agents", summary: "Two sentences. Really.", tags: ["llm"], related: [], body: "## Body" };
const quiet = { warn: () => {} };

function runner(...replies) {
  const prompts = [];
  return {
    prompts,
    run: async (p) => { prompts.push(p); return replies.length ? replies.shift() : null; },
  };
}

test("a valid reply is used as is, in one call, with the schema in the prompt", async () => {
  const r = runner(JSON.stringify(GOOD));
  const out = await processWithSchema(r.run, "BASE", quiet);
  assert.deepEqual(out, { fields: GOOD, via: "structured", attempts: 1 });
  assert.equal(r.prompts.length, 1);
  assert.ok(r.prompts[0].startsWith("BASE"));
  assert.match(r.prompts[0], /wiki_page_fields/);
});

test("a ```json fenced reply validates (the double-fence case)", async () => {
  const r = runner("```json\n" + JSON.stringify(GOOD) + "\n```");
  assert.equal((await processWithSchema(r.run, "B", quiet)).via, "structured");
});

test("a malformed reply is retried, then the valid one is used", async () => {
  const r = runner('{"title": "t", "body": "b",}', JSON.stringify(GOOD));
  const out = await processWithSchema(r.run, "B", quiet);
  assert.equal(out.via, "structured");
  assert.equal(out.attempts, 2);
  assert.equal(r.prompts[0], r.prompts[1], "the retry asks the same thing");
});

test("a reply missing a required field is retried, not accepted", async () => {
  const { tags, ...noTags } = GOOD;
  const r = runner(JSON.stringify(noTags), JSON.stringify(GOOD));
  assert.equal((await processWithSchema(r.run, "B", quiet)).attempts, 2);
});

test("budget spent: the old parse of the last reply is the soft landing, and it says so", async () => {
  const prose = "Here you go: " + JSON.stringify({ ...GOOD, extra: 1 }) + " Hope it helps.";
  const r = runner(prose, prose, prose);
  const warns = [];
  const out = await processWithSchema(r.run, "B", { warn: (...a) => warns.push(a) });
  assert.equal(r.prompts.length, 1 + PAGE_FIELDS_OUTPUT.maxRetries);
  assert.equal(out.via, "fallback");
  assert.equal(out.fields.title, GOOD.title);
  assert.equal(warns.length, 1);
  assert.match(warns[0][0], /structured output failed/);
  assert.ok(warns[0][1].lastOutput.includes("Hope it helps"), "raw output logged");
});

test("budget spent with nothing usable: null, logged", async () => {
  const r = runner("no", "still no", "nope");
  const warns = [];
  assert.equal(await processWithSchema(r.run, "B", { warn: (...a) => warns.push(a) }), null);
  assert.match(warns[0][0], /no usable fields/);
});

test("a failed or timed-out call is not retried", async () => {
  const r = runner(null);
  assert.equal(await processWithSchema(r.run, "B", quiet), null);
  assert.equal(r.prompts.length, 1);
});

test("plugin.js's llmProcessContent goes through processWithSchema, no bare regex parse", () => {
  const src = require("fs").readFileSync(require("path").join(__dirname, "../src/athena/plugin.js"), "utf8");
  const start = src.indexOf("async llmProcessContent(");
  const body = src.slice(start, src.indexOf("\n  }\n", start));
  assert.match(body, /processWithSchema\(/);
  assert.doesNotMatch(body, /JSON\.parse/);
});
