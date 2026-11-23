/**
 * test-kb-trash-delegation.js — node --test
 *
 * #180 follow-through (2026-11-19). The chat surface's `kb remove`, `kb undo`
 * and `kb rename` used to be their own JS implementations, writing a second
 * trash shape (.kb-trash/<UTC>_kb-<verb>/, no manifest) that disagreed with
 * bin/kb's:
 *   - plugin undo of a bin/kb batch moved deleted/…, modified/… and
 *     manifest.json into the vault root instead of restoring the files;
 *   - on the same day the plugin's "latest" was always a bin/kb batch
 *     ('_' sorts after 'T'), and bin/kb's undo skipped plugin bundles;
 *   - plugin remove left wikilinks and index counts stale; plugin rename
 *     wrote no snapshot at all, so it could not be undone.
 * Now all three run `bin/kb <verb> … --yes` through runMechanical: one
 * implementation, one trash shape, every operation in the audit log with
 * source "plugin". They are reached only after the user said yes to the
 * ask-first prompt (kb-confirm.js, tests/test-kb-confirm.js), hence --yes.
 *
 * plugin.js imports Obsidian, so the methods are lifted by brace-matching
 * (as tests/test-find-duplicate-dead-page.js does). ATHENA_PLUGIN_SRC runs it
 * against an older plugin.js to watch it fail.
 */
"use strict";

const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("fs");
const path = require("path");

const REPO = path.resolve(__dirname, "..");
const SRC = process.env.ATHENA_PLUGIN_SRC || path.join(REPO, "src/athena/plugin.js");
const src = fs.readFileSync(SRC, "utf8");

function liftMethod(signature) {
  const at = src.indexOf(`\n  ${signature} {`);
  if (at < 0) throw new Error(`no method ${signature}`);
  let i = src.indexOf("{", at), depth = 0;
  for (let j = i; j < src.length; j++) {
    if (src[j] === "{") depth++;
    else if (src[j] === "}" && --depth === 0) return src.slice(at + 1, j + 1);
  }
  throw new Error(`unbalanced braces in ${signature}`);
}

const SIGS = { remove: "async _kbRemove(args)", undo: "async _kbUndo()", rename: "async _kbRename(args)" };

function build(reply = { ok: true, stdout: "Done.", stderr: "" }) {
  const body = [...Object.values(SIGS), "async _kbDelegate(verb, args, label, summary)"]
    .map(liftMethod).join("\n");
  const Klass = new Function("fs", "path", `return class { ${body} }`)(fs, path);
  const k = new Klass();
  k.calls = [];
  k.runMechanical = async (command, args, idle, view, extraEnv) => {
    k.calls.push({ command, args, extraEnv });
    return typeof reply === "function" ? reply(command, args) : reply;
  };
  k.app = { vault: { adapter: { basePath: "/nonexistent-vault" } } };
  return k;
}

test("remove runs bin/kb remove <page> --yes --with-raw (page and raw together, as documented), quotes stripped, audited as plugin", async () => {
  const k = build();
  await k._kbRemove(['"Foo Bar"']);
  assert.deepEqual(k.calls.map((c) => [c.command, c.args]), [["remove", ["Foo Bar", "--yes", "--with-raw"]]]);
  assert.equal(k.calls[0].extraEnv.ATHENA_AUDIT_SOURCE, "plugin");
});

test("undo runs bin/kb undo --yes", async () => {
  const k = build();
  await k._kbUndo();
  assert.deepEqual(k.calls.map((c) => [c.command, c.args]), [["undo", ["--yes"]]]);
  assert.equal(k.calls[0].extraEnv.ATHENA_AUDIT_SOURCE, "plugin");
});

test("rename runs bin/kb rename <old> --to <new> --yes", async () => {
  const k = build();
  await k._kbRename(['"Old Name"', "--to", "'New Name'"]);
  assert.deepEqual(k.calls.map((c) => [c.command, c.args]),
    [["rename", ["Old Name", "--to", "New Name", "--yes"]]]);
});

test("usage errors never reach bin/kb", async () => {
  const k = build();
  assert.match((await k._kbRemove([""])).message, /Usage/);
  assert.match((await k._kbRename(["Only"])).message, /Usage/);
  assert.equal(k.calls.length, 0);
});

test("bin/kb's own output is what the chat shows; a failure says it failed", async () => {
  const ok = build({ ok: true, stdout: "Trashed 2 file(s) → .kb-trash/\nDone.", stderr: "" });
  assert.match((await ok._kbRemove(["P"])).message, /Trashed 2 file\(s\)/);
  const bad = build({ ok: false, stdout: "", stderr: "Page not found: P" });
  const r = await bad._kbRemove(["P"]);
  assert.match(r.message, /failed/i);
  assert.match(r.message, /Page not found: P/);
});

test("none of the three touches the vault itself any more", () => {
  for (const sig of Object.values(SIGS)) {
    const body = liftMethod(sig);
    assert.doesNotMatch(body, /fs\.(renameSync|writeFileSync|rmSync|mkdirSync)/, sig);
    assert.doesNotMatch(body, /_kbTrashBundle\(/, sig);
  }
});

test("runMechanical passes extraEnv through to the child", () => {
  const body = liftMethod("runMechanical(command, args, idleTimeoutMs = null, view = null, extraEnv = null)");
  assert.match(body, /\.\.\.\(extraEnv \|\| \{\}\)/);
});
