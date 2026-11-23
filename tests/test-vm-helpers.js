/**
 * test-vm-helpers.js — node --test
 *
 * scripts/vm-tests/specs/_helpers.js kept its own copy of the old Obsidian
 * fixture (from 10e1f3ad), with the trust-prompt bug argus fixed in #14:
 * `isVisible({ timeout })` ignores its timeout, so a prompt that painted after
 * the 2 s boot sleep was missed, and the two Escapes then closed it WITHOUT
 * trusting: restricted mode, no plugin loaded, and a VM gate that can't tell.
 * The fixture is now argus's obsidianSpec, gated on a readyCheck that waits
 * for Athena to finish onload and disable the standalone Gryphon
 * (specs/_ready.js), not on fixed sleeps.
 */
"use strict";

const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("fs");
const path = require("path");

const SPECS = path.join(__dirname, "..", "scripts", "vm-tests", "specs");
const { athenaReady, pluginLoadState, makeReadyCheck } = require(path.join(SPECS, "_ready.js"));

// The designed end state (plugin.js:913): Athena's onload disables the
// standalone Gryphon (mutual exclusivity), so by default gryphon is neither
// loaded nor enabled. `gryphon` / `gryphonEnabled` put it back.
function app({ gryphon = false, gryphonEnabled = false, athena = true, settings = true,
               runMechanical = true, view = true, command = true } = {}) {
  const plugins = {};
  if (gryphon) plugins.gryphon = {};
  if (athena) {
    plugins.athena = {};
    if (settings) plugins.athena.settings = {};
    if (runMechanical) plugins.athena.runMechanical = () => {};
  }
  const enabled = new Set(["athena"]);
  if (gryphonEnabled) enabled.add("gryphon");
  return {
    plugins: { plugins, enabledPlugins: enabled },
    viewRegistry: { viewByType: view ? { "athena-view": () => {} } : {} },
    commands: { commands: command ? { "athena:open-chat": {} } : {} },
  };
}

test("ready once Athena has loaded and has disabled the standalone Gryphon (matrix -4)", () => {
  assert.equal(athenaReady(app()), true, "athena loaded, gryphon disabled: the designed end state");
  for (const missing of ["athena", "settings", "runMechanical", "view", "command"]) {
    assert.equal(athenaReady(app({ [missing]: false })), false, `not ready without ${missing}`);
  }
  // Matrix -4 waited 120 s for both to be loaded, which Athena never allows.
  assert.equal(athenaReady(app({ gryphon: true, gryphonEnabled: true, athena: false })), false,
    "gryphon up, athena not yet: athena's onload has not run");
  assert.equal(athenaReady(app({ gryphon: true, gryphonEnabled: true })), false,
    "both loaded: athena has not disabled gryphon yet");
  assert.equal(athenaReady(app({ gryphonEnabled: true })), false, "gryphon still enabled: the disable has not been saved");
  assert.equal(athenaReady(app({ gryphon: true })), false, "gryphon still loaded: the disable has not unloaded it");
  assert.equal(athenaReady(undefined), false, "no window.app yet");
  assert.equal(athenaReady({}), false, "app without plugins");
  const noSet = app(); delete noSet.plugins.enabledPlugins;
  assert.equal(athenaReady(noSet), false, "no enabledPlugins to check: can't tell gryphon is off");
});

test("_helpers.js uses argus's obsidianSpec with that readyCheck, not its own fixture copy", () => {
  const src = fs.readFileSync(path.join(SPECS, "_helpers.js"), "utf8");
  assert.match(src, /require\("@shared\/argus"\)\.helpers\.obsidianSpec/);
  assert.match(src, /playwrightTest: require\("@playwright\/test"\)/, "the runner's own Playwright (OR-9)");
  assert.match(src, /readyCheck: makeReadyCheck\(\)/, "the diagnostic readyCheck, built on athenaReady");
  assert.match(src, /readyTimeoutMs: 120_000/, "readiness has its own budget; argus's 30 s default failed matrix -3");
  assert.match(src, /basePort: 9322,\s*maxPort: 9399/, "ports stay offset from gryphon's");
  for (const [re, why] of [
    [/isVisible\(/, "isVisible ignores its timeout (argus #14)"],
    [/function dismissTrustModal/, "no local trust-prompt copy"],
    [/connectOverCDP/, "no local launch/CDP copy"],
    [/keyboard\.press\("Escape"\)/, "Escape can close the trust prompt without trusting"],
  ]) {
    assert.doesNotMatch(src, re, why);
  }
});

test("pluginLoadState tells files-missing, not-enabled and onload-failed apart", () => {
  const ps = (manifests, enabled, plugins) => ({ plugins: { manifests, enabledPlugins: new Set(enabled), plugins } });
  assert.deepEqual(pluginLoadState(ps({ gryphon: {}, athena: {} }, ["gryphon", "athena"], { athena: {} })),
    { gryphon: { known: true, enabled: true, loaded: false }, athena: { known: true, enabled: true, loaded: true } },
    "known && enabled && !loaded: gryphon's onload failed");
  assert.deepEqual(pluginLoadState(ps({ athena: {} }, ["gryphon", "athena"], {})).gryphon,
    { known: false, enabled: true, loaded: false }, "no manifest: the files never reached the vault");
  assert.deepEqual(pluginLoadState(ps({ gryphon: {} }, [], {})).gryphon,
    { known: true, enabled: false, loaded: false }, "not enabled");
  assert.deepEqual(pluginLoadState(undefined).gryphon, { known: false, enabled: false, loaded: false }, "no window.app yet");
  // It runs inside the page as source text: it must be self-contained.
  const inPage = new Function("app", `return (${pluginLoadState})(app)`);
  assert.deepEqual(inPage(ps({ gryphon: {} }, ["gryphon"], { gryphon: {} })).gryphon, { known: true, enabled: true, loaded: true });
});

test("makeReadyCheck returns athenaReady, logs state changes once each, and the page's errors", async () => {
  const handlers = {};
  let state = { manifests: { gryphon: {}, athena: {} }, enabledPlugins: new Set(["gryphon", "athena"]), plugins: {} };
  const page = {
    on: (ev, fn) => { (handlers[ev] = handlers[ev] || []).push(fn); },
    // The page runs the expression against its window.app; here, a fake one.
    evaluate: async (expr) => new Function("window", `return ${expr}`)({ app: { ...app(), plugins: state } }),
  };
  const lines = [];
  const check = makeReadyCheck({ log: (m) => lines.push(m) });

  assert.equal(await check(page), false);
  assert.equal(await check(page), false);
  assert.equal(lines.filter((l) => /plugin state/.test(l)).length, 1, "an unchanged state is logged once");
  assert.match(lines[0], /ready=false .*"gryphon":\{"known":true,"enabled":true,"loaded":false\}/);

  handlers.console[0]({ type: () => "error", text: () => "Plugin failure: gryphon TypeError: x is undefined" });
  handlers.console[0]({ type: () => "log", text: () => "noise" });
  handlers.pageerror[0](new Error("boom"));
  assert.ok(lines.some((l) => /console error: Plugin failure: gryphon/.test(l)));
  assert.ok(!lines.some((l) => /noise/.test(l)), "only console errors");
  assert.ok(lines.some((l) => /pageerror: Error: boom/.test(l)));

  state = { ...state, enabledPlugins: new Set(["athena"]), plugins: { athena: { settings: {}, runMechanical: () => {} } } };
  assert.equal(await check(page), true, "ready once athenaReady is");
  assert.match(lines.filter((l) => /plugin state/.test(l))[1], /ready=true/);
  await check(page);
  assert.equal(handlers.console.length, 1, "listeners attached once per page");
});
