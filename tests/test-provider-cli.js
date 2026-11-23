"use strict";
// Settings → Athena → "Test Connection" finds each provider's CLI the way
// Gryphon does (provider-cli.js), Antigravity included.
const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("fs");
const path = require("path");
const { cliForProvider, CLI_KINDS } = require("../src/athena/provider-cli");

const deps = {
  resolveClaudeBinary: () => "/opt/claude",
  findCodexBinary: () => "/home/u/.local/bin/codex",
  findGeminiBinary: () => null,
  findAntigravityBinary: () => "/home/u/.local/bin/agy",
};

test("Antigravity is tested, through Gryphon's finder", () => {
  assert.deepEqual(cliForProvider("antigravity-cli", {}, deps),
    { kind: "cli", label: "Antigravity CLI", bin: "/home/u/.local/bin/agy" });
});

test("Codex resolves to a full path, not a bare name on PATH", () => {
  assert.equal(cliForProvider("codex-cli", {}, deps).bin, "/home/u/.local/bin/codex");
});

test("Claude and auto keep Athena's own resolver", () => {
  for (const p of ["claude-code", "auto", "", undefined]) {
    assert.equal(cliForProvider(p, {}, deps).bin, "/opt/claude", String(p));
  }
});

test("not installed reads as bin null; a throwing finder too", () => {
  assert.equal(cliForProvider("gemini-cli", {}, deps).bin, null);
  const orig = console.warn; console.warn = () => {};
  try {
    const r = cliForProvider("codex-cli", {}, { ...deps, findCodexBinary: () => { throw new Error("x"); } });
    assert.equal(r.bin, null);
  } finally { console.warn = orig; }
});

test("API and unknown providers are told apart", () => {
  assert.equal(cliForProvider("google-api", {}, deps).kind, "api");
  assert.deepEqual(cliForProvider("future-cli", {}, deps), { kind: "unknown", label: "future-cli" });
});

test("every CLI kind Gryphon can construct has an entry", () => {
  const factory = fs.readFileSync(path.join(__dirname, "..",
    "vendor/gryphon/packages/provider-runtime/src/factory.ts"), "utf8");
  const kinds = new Set([...factory.matchAll(/kind === "([a-z]+-cli|claude-code)"/g)].map((m) => m[1]));
  assert.ok(kinds.has("antigravity-cli"), [...kinds].join(","));
  for (const k of kinds) assert.ok(CLI_KINDS[k], `no Test Connection entry for ${k}`);
});

test("the Gryphon finders it uses are exported by the vendored runtime", () => {
  const index = fs.readFileSync(path.join(__dirname, "..",
    "vendor/gryphon/packages/provider-runtime/src/index.ts"), "utf8");
  for (const f of ["findCodexBinary", "findGeminiBinary", "findAntigravityBinary"]) {
    assert.match(index, new RegExp(`export const ${f} =`));
  }
});

test("plugin.js has no hand-written bare-name CLI map left", () => {
  const src = fs.readFileSync(path.join(__dirname, "..", "src/athena/plugin.js"), "utf8");
  assert.doesNotMatch(src, /bin:\s*"(codex|gemini|agy)"/);
  assert.match(src, /cliForProvider\(provider, this\.plugin\)/);
});
