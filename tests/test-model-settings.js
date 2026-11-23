/**
 * test-model-settings.js — node --test
 *
 * Gryphon's model-selection change (gryphon PR #44): Athena keeps its own
 * settings (data.json), so Gryphon's load-time model migration never reaches
 * them, and llmProcessContent passed `settings.model || "sonnet"` straight to
 * the claude CLI as --model. src/athena/model-settings.js routes both through
 * Gryphon's registry. Expected ids come from the vendored registry itself,
 * never hardcoded, so the tests stay true across a registry bump.
 */
"use strict";

const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("fs");
const path = require("path");

const ROOT = path.resolve(__dirname, "..");
const SRC = path.join(ROOT, "src", "athena");
const PC = path.join(ROOT, "vendor", "gryphon", "packages", "provider-config", "dist");
const registry = require(path.join(PC, "registry.js"));
const anthropic = require(path.join(PC, "pricing", "anthropic.js"));
const { cliModelArg, migrateModelSettings, modelForCli } = require(path.join(SRC, "model-settings.js"));

const quiet = (fn) => { const w = console.warn; console.warn = () => {}; try { return fn(); } finally { console.warn = w; } };

// ── site 3: the claude CLI --model argument ────────────────────────────

test("aliases and an empty setting resolve to concrete Claude ids via Gryphon", () => {
  for (const alias of ["sonnet", "haiku", "opus"]) {
    const got = cliModelArg({ model: alias });
    assert.equal(got, anthropic.resolveModel(alias));
    assert.match(got, /^claude-/);
  }
  assert.equal(cliModelArg({ model: "" }), anthropic.DEFAULT_MODEL);
  assert.equal(cliModelArg({}), anthropic.DEFAULT_MODEL);
  assert.equal(cliModelArg(null), anthropic.DEFAULT_MODEL);
});

test("a retired Claude id is sent as its successor", () => {
  const retired = Object.keys(registry.RETIRED_MODEL_MIGRATION).find((id) => id.startsWith("claude-"));
  assert.ok(retired, "the registry has at least one retired Claude id");
  assert.equal(cliModelArg({ model: retired }), registry.retiredModelSuccessor(retired));
});

test("a current or brand-new Claude id passes through unchanged", () => {
  assert.equal(cliModelArg({ model: anthropic.DEFAULT_MODEL }), anthropic.DEFAULT_MODEL);
  assert.equal(cliModelArg({ model: "claude-future-9" }), "claude-future-9");
});

test("non-Claude models are refused (null), never passed to the claude CLI", () => {
  const nonClaude = registry.allModels().filter((m) => m.vendor !== "anthropic").map((m) => m.id);
  assert.ok(nonClaude.length > 0);
  for (const id of nonClaude) assert.equal(quiet(() => cliModelArg({ model: id })), null, id);
});

test("vault-supplied values that aren't a Claude id are refused, not passed as argv", () => {
  for (const bad of ["--dangerously-skip-permissions", "-p", "claude-x --y", "claude-x\n", "../claude", "CLAUDE-OPUS", 42, {}]) {
    assert.equal(quiet(() => cliModelArg({ model: bad })), null, JSON.stringify(bad));
  }
});

test("a refused model is never skipped silently: one notice per session, a log every time", () => {
  const plugin = { settings: { model: "gpt-6.1-sol" } };
  const notices = [];
  const warns = [];
  const w = console.warn; console.warn = (...a) => warns.push(a.join(" "));
  try {
    assert.equal(modelForCli(plugin, { notify: (m) => notices.push(m) }), null);
    assert.equal(modelForCli(plugin, { notify: (m) => notices.push(m) }), null);
  } finally { console.warn = w; }
  assert.equal(notices.length, 1, "notified once per session, not on every capture");
  assert.match(notices[0], /gpt-6\.1-sol/);
  assert.match(notices[0], /isn't a Claude model/);
  assert.match(notices[0], /skipped/);
  assert.ok(warns.length >= 2, "logged on every skip");
});

test("the notice shows a bounded, single-line copy of the vault value", () => {
  const plugin = { settings: { model: "x".repeat(500) + "\n--evil" } };
  const notices = [];
  quiet(() => modelForCli(plugin, { notify: (m) => notices.push(m) }));
  assert.equal(notices.length, 1);
  assert.ok(notices[0].length < 300);
  assert.doesNotMatch(notices[0], /\n/);
});

test("a valid model returns its id and raises no notice", () => {
  const notices = [];
  assert.equal(modelForCli({ settings: { model: "sonnet" } }, { notify: (m) => notices.push(m) }),
    anthropic.resolveModel("sonnet"));
  assert.equal(notices.length, 0);
});

test("plugin.js's CLI spawn takes its --model from modelForCli only, with a Notice", () => {
  const src = fs.readFileSync(path.join(SRC, "plugin.js"), "utf8");
  assert.doesNotMatch(src, /settings\.model\s*\|\|\s*["']sonnet["']/, "no raw fallback to the bare alias");
  assert.match(src, /modelForCli\(this,\s*\{\s*notify:[^}]*new Notice\(/);
});

// ── Athena's own settings migration ────────────────────────────────────

test("a legacy alias in model is migrated the way Gryphon migrates its own", () => {
  const aliases = registry.legacyAliasMigrationFor("anthropic");
  const [alias, to] = Object.entries(aliases)[0];
  const s = { model: alias };
  const changes = migrateModelSettings(s);
  assert.equal(s.model, registry.retiredModelSuccessor(to) || to);
  assert.ok(changes.some((c) => c.key === "model" && c.from === alias));
});

test("retired ids in model and fallbackModel move to their successors", () => {
  const [a, b] = Object.keys(registry.RETIRED_MODEL_MIGRATION);
  const s = { model: a, fallbackModel: b };
  const changes = migrateModelSettings(s);
  assert.equal(s.model, registry.RETIRED_MODEL_MIGRATION[a]);
  assert.equal(s.fallbackModel, registry.RETIRED_MODEL_MIGRATION[b]);
  assert.equal(changes.length, 2);
});

test("current ids, unknown ids and non-strings are left alone; a second run is a no-op", () => {
  const s = { model: anthropic.DEFAULT_MODEL, fallbackModel: "claude-future-9", other: "sonnet" };
  assert.deepEqual(migrateModelSettings(s), []);
  assert.deepEqual(s, { model: anthropic.DEFAULT_MODEL, fallbackModel: "claude-future-9", other: "sonnet" });
  const t = { model: 7 };
  assert.deepEqual(migrateModelSettings(t), []);
  const u = { model: Object.keys(registry.RETIRED_MODEL_MIGRATION)[0] };
  migrateModelSettings(u);
  assert.deepEqual(migrateModelSettings(u), []);
});

test("loadSettings runs the migration and persists only when something changed", () => {
  const src = fs.readFileSync(path.join(SRC, "plugin.js"), "utf8");
  const body = src.slice(src.indexOf("async loadSettings()"), src.indexOf("async saveSettings()"));
  assert.match(body, /migrateModelSettings\(this\.settings\)/);
  assert.match(body, /\.length\s*>\s*0[\s\S]*saveData\(this\.settings\)/);
});
