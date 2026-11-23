"use strict";
// No Athena source hardcodes a Claude model id that Gryphon's registry
// retires. Gryphon 2.13.0 retired claude-haiku-4-5 and claude-fable-5-1;
// bin/lib/canonical_source.py still named claude-haiku-4-5. The list comes
// from the vendored registry, so the next Gryphon bump re-checks itself.
const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("fs");
const path = require("path");

const ROOT = path.join(__dirname, "..");
const registry = require(path.join(ROOT, "vendor/gryphon/packages/provider-config/dist/registry.js"));
const RETIRED = Object.keys(registry.RETIRED_MODEL_MIGRATION).filter((id) => id.startsWith("claude-"));

// scripts/ too: it held claude-opus-4-7 (bulk regen), claude-opus-4-8 (the
// VM fixture) and a hand-copied timeout table after the src/ sweep (#182).
const SCAN = ["src", "bin/lib", "scripts", "server"];

function* sources(dir) {
  for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
    if (e.name === "node_modules" || e.name === "__pycache__") continue;
    const p = path.join(dir, e.name);
    if (e.isDirectory()) yield* sources(p);
    else if (/\.(js|py|ts)$/.test(e.name)) yield p;
  }
}

test("the registry lists retired Claude ids", () => {
  assert.ok(RETIRED.includes("claude-haiku-4-5"), RETIRED.join(","));
});

test("no source names a retired Claude model id", () => {
  const hits = [];
  for (const dir of SCAN) {
    const abs = path.join(ROOT, dir);
    if (!fs.existsSync(abs)) continue;
    for (const file of sources(abs)) {
      fs.readFileSync(file, "utf8").split("\n").forEach((line, i) => {
        for (const id of RETIRED) {
          // quoted as a value; a comment naming the old id is fine
          if (new RegExp(`['"\`]${id.replace(/[.]/g, "\\.")}(\\[1m\\])?['"\`]`).test(line)) {
            hits.push(`${path.relative(ROOT, file)}:${i + 1}: ${id}`);
          }
        }
      });
    }
  }
  assert.deepEqual(hits, []);
});

// #182 acceptance: no hardcoded Claude model id in tracked source at all.
// Ids come from Gryphon's registry (model-settings.js, scripts/_athena_models.py);
// aliases ("haiku", "opus", "sonnet") are fine, the CLI and Gryphon resolve them.
test("no source hardcodes a concrete Claude model id", () => {
  const ID = /['"`]claude-(?:opus|sonnet|haiku|fable)-\d[\w.-]*(?:\[1m\])?['"`]/;
  const hits = [];
  for (const dir of SCAN) {
    const abs = path.join(ROOT, dir);
    if (!fs.existsSync(abs)) continue;
    for (const file of sources(abs)) {
      fs.readFileSync(file, "utf8").split("\n").forEach((line, i) => {
        if (/^\s*(#|\/\/|\*)/.test(line)) return;  // a comment naming an old id
        if (ID.test(line)) hits.push(`${path.relative(ROOT, file)}:${i + 1}: ${line.trim()}`);
      });
    }
  }
  assert.deepEqual(hits, []);
});

