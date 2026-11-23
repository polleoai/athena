/**
 * test-plugin-same-source.js — node --test
 *
 * The plugin's browser capture picks its raw path with one Python call
 * (src/athena/plugin.js). Since 2026-11-22 that call first asks
 * raw_writer.existing_raw_for whether a raw of this category already records
 * the source, and the plugin then writes THAT raw: the plugin and the CLI had
 * captured arxiv.org/abs/2605.00055 18 s apart into two raws.
 *
 * The Python is a string inside plugin.js, where a quoting or newline slip
 * fails only at runtime (the catch then falls back to a local slug, silently
 * re-creating twins). So this pulls the exact string out of plugin.js and runs
 * it, with the vault's bin/lib, against a temp vault.
 */
"use strict";

const { test } = require("node:test");
const assert = require("node:assert/strict");
const { execFileSync } = require("child_process");
const fs = require("fs");
const os = require("os");
const path = require("path");

const ROOT = path.join(__dirname, "..");
const SRC = fs.readFileSync(path.join(ROOT, "src", "athena", "plugin.js"), "utf8");
const PY = process.env.ATHENA_PYTHON || path.join(os.homedir(), ".athena", "venv", "bin", "python");

function embeddedSnippet() {
  const i = SRC.indexOf("Same-source twin guard");
  assert.ok(i > 0, "the guard is in plugin.js");
  const from = SRC.indexOf('"-c",', i) + '"-c",'.length;
  const to = SRC.indexOf("// Plugin-bundled bin/lib/", from);
  // The snippet is a `+`-chain of double-quoted literals: join them, no eval.
  const literals = SRC.slice(from, to).match(/"(?:[^"\\]|\\.)*"/g) || [];
  assert.ok(literals.length > 3, "found the snippet's string literals");
  return literals.map((l) => JSON.parse(l)).join("");
}

function run(vault, category, url) {
  return execFileSync(PY, ["-c", embeddedSnippet(), path.join(ROOT, "bin", "lib"), category, url, "T", vault],
    { encoding: "utf8" }).trim();
}

function vault() {
  const v = fs.mkdtempSync(path.join(os.tmpdir(), "athena-plugin-twin-"));
  fs.mkdirSync(path.join(v, "raw", "papers", "artifacts"), { recursive: true });
  return v;
}

const havePy = fs.existsSync(PY);

test("an existing raw for the same source is returned (pdf URL vs abs URL)", { skip: !havePy && "no Athena python" }, () => {
  const v = vault();
  const raw = path.join(v, "raw", "papers", "artifacts", "arxiv-2605.00055.md");
  fs.writeFileSync(raw, '---\ntitle: "P"\nsource: "https://arxiv.org/abs/2605.00055"\n---\n\nbody\n');
  assert.equal(run(v, "papers", "https://arxiv.org/pdf/2605.00055v2"), "EXISTING:" + raw);
});

test("no raw for the source: the canonical slug, as before", { skip: !havePy && "no Athena python" }, () => {
  const out = run(vault(), "papers", "https://arxiv.org/abs/2605.00055");
  assert.ok(!out.startsWith("EXISTING:"), out);
  assert.match(out, /2605[-.]00055/);
});

test("plugin.js writes the existing raw when the call says EXISTING", () => {
  const i = SRC.indexOf("Same-source twin guard");
  const block = SRC.slice(i, i + 3200);
  assert.match(block, /_out\.startsWith\("EXISTING:"\)/);
  assert.match(block, /rawFilePath = _existingRaw && /);
});
