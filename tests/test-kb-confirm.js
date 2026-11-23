/**
 * test-kb-confirm.js — node --test
 *
 * Chat kb commands that modify or destroy ask first (the user, 2026-11-19:
 * "we should ask first"), following CLAUDE.md's confirmation levels:
 * modifying (create, rename, move, merge, insight "Title") confirm first;
 * destructive (remove, purge, ungroup) ask "Proceed?"; undo asks as the CLI
 * does. Read-only and safe-write verbs run at once. A "yes" runs it with
 * --yes where bin/kb takes one (so bin/kb never prompts again on a pipe);
 * anything else cancels and is logged as a deny.
 */
"use strict";

const { test } = require("node:test");
const assert = require("node:assert/strict");
const c = require("../src/athena/kb-confirm");
const { detectMechanicalCommand } = require("../src/athena/kb-commands");

const m = (text) => detectMechanicalCommand(text);

test("the CLAUDE.md modifying + destructive verbs, and undo, need a yes", () => {
  for (const t of ['kb remove Some Page', 'kb rename A --to "B"', 'kb move A --into "Hub"',
                   "kb merge A B", "kb ungroup Hub", "kb purge", "kb undo",
                   "kb create Hub --topic", 'kb insight "A finding"']) {
    assert.ok(c.needsConfirm(m(t)), t);
  }
});

test("read-only and safe-write verbs run at once", () => {
  for (const t of ["kb stats", "kb lint", "kb index", "kb trash", "kb list", "kb search agents",
                   'kb journal "x"', "kb rules", "kb insight --list", "kb audit tail",
                   "kb audit grep remove", "kb add https://e.com/a", "kb reflect"]) {
    assert.ok(m(t), `detected: ${t}`);
    assert.equal(c.needsConfirm(m(t)), false, t);
  }
});

test("the preview names the verb, its target and what happens", () => {
  const p = c.describe(m("kb remove Some Page"));
  assert.match(p, /Some Page/);
  assert.match(p, /\.kb-trash/);
  assert.match(p, /kb undo/);
  assert.match(p, /\byes\b/);
  assert.match(c.describe(m("kb purge")), /permanent/i);
  assert.match(c.describe(m("kb ungroup Hub")), /independent/);
});

test("a yes runs with --yes only where bin/kb takes it", () => {
  assert.deepEqual(c.confirmedArgs(m("kb ungroup Hub")), ["Hub", "--yes"]);
  assert.deepEqual(c.confirmedArgs(m("kb merge A B")), ["A", "B", "--yes"]);
  assert.deepEqual(c.confirmedArgs(m("kb create Hub --topic")), ["Hub", "--topic", "--yes"]);
  assert.deepEqual(c.confirmedArgs(m("kb purge")), [], "purge parses its first arg as days");
  assert.deepEqual(c.confirmedArgs(m('kb move A --into "Hub"')), ["A", "--into", "Hub"]);
  assert.deepEqual(c.confirmedArgs(m('kb insight "T"')), ["T"]);
});

test("isYes accepts only a plain yes", () => {
  for (const t of ["yes", "Yes", " y ", "YES", "proceed", "yes."]) assert.ok(c.isYes(t), t);
  for (const t of ["no", "n", "yes please remove the other one", "", "kb remove X", "sure"]) {
    assert.equal(c.isYes(t), false, t);
  }
});

test("a pending confirmation expires", () => {
  const p = c.pending(m("kb remove P"), 1000);
  assert.equal(c.expired(p, 1000 + c.TTL_MS - 1), false);
  assert.equal(c.expired(p, 1000 + c.TTL_MS + 1), true);
});

test("the audit tool name and summary for a declined prompt", () => {
  assert.deepEqual(c.auditOf(m("kb remove Some Page")), ["kb remove", "Some Page"]);
  assert.deepEqual(c.auditOf(m('kb rename A --to "B"')), ["kb rename", "A --to B"]);
});

test("kb audit tail / grep are routed to bin/kb", () => {
  assert.deepEqual(m("kb audit tail"), { command: "audit", args: ["tail"] });
  assert.deepEqual(m("kb audit tail 5"), { command: "audit", args: ["tail", "5"] });
  assert.deepEqual(m("kb audit grep Some Page"), { command: "audit", args: ["grep", "Some Page"] });
  assert.equal(m("kb audit record a b deny c"), null, "record is internal, never typed in chat");
});

test("plugin.js gates before it dispatches, and closes the child's stdin", () => {
  const src = require("fs").readFileSync(require("path").join(__dirname, "../src/athena/plugin.js"), "utf8");
  const run = src.slice(src.indexOf("async _runKbCommandAsync("));
  const gate = run.indexOf("needsConfirm(");
  assert.ok(gate > 0, "the gate is in _runKbCommandAsync");
  assert.ok(gate < run.indexOf('mechanical.command === "remove"'), "before any verb runs");
  const handle = src.slice(src.indexOf("_handleKbCommand(text) {"), src.indexOf("async _runKbCommandAsync("));
  assert.match(handle, /_pendingKb/, "a reply is checked against the pending confirmation first");
  const mech = src.slice(src.indexOf("runMechanical(command, args"));
  assert.match(mech.slice(0, 3000), /proc\.stdin\.end\(\)/, "a bin/kb prompt gets EOF, never hangs");
});
