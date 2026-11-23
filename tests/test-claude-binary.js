/**
 * test-claude-binary.js — node --test
 *
 * #217: Athena must never spawn the Claude Code path named in the vault's
 * data.json (settings.claudePath). A shared, synced or cloned vault could
 * point it at a script it ships. The binary comes from Gryphon's
 * resolveCliPath (machine-confirmed path or auto-detection), via
 * src/athena/claude-binary.js.
 */
"use strict";

const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("fs");
const os = require("os");
const path = require("path");

const ROOT = path.resolve(__dirname, "..");
const SRC = path.join(ROOT, "src", "athena");
const { resolveClaudeBinary, ATHENA_SECURITY_HOST_ID } = require(path.join(SRC, "claude-binary.js"));

function codeLines(file) {
  return fs.readFileSync(file, "utf8").split("\n")
    .map((l, i) => ({ l, n: i + 1 }))
    .filter(({ l }) => !/^\s*(\/\/|\*|\/\*)/.test(l));
}

test("no Athena source spawns settings.claudePath", () => {
  const hits = [];
  for (const f of fs.readdirSync(SRC).filter((f) => f.endsWith(".js"))) {
    for (const { l, n } of codeLines(path.join(SRC, f))) {
      if (/settings\.claudePath\b/.test(l)) hits.push(`${f}:${n}: ${l.trim()}`);
    }
  }
  assert.deepEqual(hits, []);
});

test("every Claude Code spawn site in plugin.js goes through resolveClaudeBinary", () => {
  const src = fs.readFileSync(path.join(SRC, "plugin.js"), "utf8");
  assert.ok((src.match(/resolveClaudeBinary\(this(\.plugin)?\)/g) || []).length >= 2,
    "setup notice and llmProcessContent");
  // Test Connection's claude-code and auto entries moved to provider-cli.js
  // (Gryphon 2.13.0 integration); they still go through the resolver.
  const cli = fs.readFileSync(path.join(SRC, "provider-cli.js"), "utf8");
  assert.match(cli, /require\("\.\/claude-binary"\)/);
  assert.match(cli, /deps\.resolveClaudeBinary\(plugin\)/);
  assert.doesNotMatch(cli, /findClaudeBinary/, "no direct detection that bypasses the resolver");
  assert.doesNotMatch(src, /findClaudeBinary\(\)/, "no direct detection that bypasses the resolver");
});

test("a vault data.json claudePath pointing at a vault script is never returned or run", () => {
  const vault = fs.mkdtempSync(path.join(os.tmpdir(), "athena-217-"));
  try {
    const sentinel = path.join(vault, "PWNED");
    const evil = path.join(vault, ".obsidian", "plugins", "athena", "evil.sh");
    fs.mkdirSync(path.dirname(evil), { recursive: true });
    fs.writeFileSync(evil, `#!/bin/sh\ntouch "${sentinel}"\necho "9.9.9 (Claude Code)"\n`, { mode: 0o755 });
    const plugin = {
      settings: { claudePath: evil },
      app: { vault: { adapter: { basePath: vault, getBasePath: () => vault }, getName: () => "v" } },
    };
    // The real resolver; detection stubbed to "nothing installed".
    const got = resolveClaudeBinary(plugin, { detect: () => null });
    assert.notEqual(got, evil);
    assert.ok(!got || !got.startsWith(vault), `resolved a path inside the vault: ${got}`);
    assert.equal(fs.existsSync(sentinel), false, "the vault script was executed");
  } finally {
    fs.rmSync(vault, { recursive: true, force: true });
  }
});

test("asks the resolver for claude-code under host 'athena', and never passes settings", () => {
  const calls = [];
  const plugin = { settings: { claudePath: "/vault/evil.sh" }, app: { id: "app" } };
  const got = resolveClaudeBinary(plugin, {
    resolve: (kind, ctx) => { calls.push([kind, ctx]); return { path: "/usr/local/bin/claude" }; },
  });
  assert.equal(got, "/usr/local/bin/claude");
  assert.equal(calls[0][0], "claude-code");
  assert.equal(calls[0][1].hostId, ATHENA_SECURITY_HOST_ID);
  assert.equal(ATHENA_SECURITY_HOST_ID, "athena");
  assert.equal(calls[0][1].app, plugin.app);
  assert.equal(calls[0][1].overrides, undefined, "no overrides from vault data");
});

test("a resolver failure falls back to detection, never to settings", () => {
  const plugin = { settings: { claudePath: "/vault/evil.sh" }, app: {} };
  const warn = console.warn; console.warn = () => {};
  try {
    const got = resolveClaudeBinary(plugin, {
      resolve: () => { throw new Error("store unreadable"); },
      detect: () => "/opt/homebrew/bin/claude",
    });
    assert.equal(got, "/opt/homebrew/bin/claude");
  } finally { console.warn = warn; }
});

test("nothing found resolves to null (the caller shows the 'not found' notice)", () => {
  assert.equal(resolveClaudeBinary({ app: {} }, { resolve: () => ({ path: null }) }), null);
});
