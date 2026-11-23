"use strict";

/**
 * Ask-first for chat kb commands that modify or destroy (the user,
 * 2026-11-19: "we should ask first").
 *
 * The verbs and levels are CLAUDE.md's: modifying (create, rename, move,
 * merge, insight "Title") confirm first; destructive (remove, purge,
 * ungroup) ask "Proceed?"; undo asks, as `bin/kb undo` does in a terminal.
 * Until now the chat surface ran them the moment they were typed (remove,
 * undo, rename), or handed them to bin/kb on a pipe where its own prompt
 * could only time out (ungroup) or never existed (purge).
 *
 * Flow (plugin.js): the first message shows describe() and keeps a pending
 * entry; a plain yes (isYes) within TTL_MS runs it with confirmedArgs(); any
 * other message, or a late yes, cancels it and logs a deny to the audit log
 * (#180) through `bin/kb audit record`.
 */

const TTL_MS = 5 * 60 * 1000;

const unq = (s) => String(s || "").trim().replace(/^["']|["']$/g, "");

// verb -> [takes --yes, describe(args)]
const VERBS = {
  remove: [true, (a) => `\`kb remove\` moves the wiki page **${unq(a[0])}** and its raw source to ` +
    "`.kb-trash/` and removes the links to it from other pages. `kb undo` restores it for 30 days."],
  rename: [true, (a) => `\`kb rename\` renames **${unq(a[0])}** to **${unq(a[2])}** and rewrites every ` +
    "wikilink to it. `kb undo` reverses it."],
  move: [false, (a) => `\`kb move\` moves **${unq(a[0])}** into the hub **${unq(a[2])}** ` +
    "(and out of its current hub). `kb undo` reverses it."],
  merge: [true, (a) => `\`kb merge\` merges **${unq(a[0])}** and **${unq(a[1])}** into one page` +
    (a[3] ? ` named **${unq(a[3])}**` : "") + "; the old pages go to `.kb-trash/`. `kb undo` reverses it."],
  ungroup: [true, (a) => `\`kb ungroup\` dissolves the hub **${unq(a[0])}**. From chat its children are ` +
    "left independent and the hub page goes to `.kb-trash/` (run `kb ungroup` in a terminal to choose " +
    "otherwise). `kb undo` reverses it."],
  purge: [false, () => "`kb purge` **permanently** deletes every trash batch older than 30 days. " +
    "This cannot be undone."],
  undo: [true, () => "`kb undo` restores the most recent batch in `.kb-trash/` (`kb trash` lists it) " +
    "to where it was."],
  create: [true, (a) => `\`kb create\` creates the hub page **${unq(a[0])}**` + (a[1] ? ` (${a[1]})` : "") + "."],
  insight: [false, (a) => `\`kb insight\` saves **${unq(a[0])}** as a permanent insight page.`],
};

function needsConfirm(mech) {
  if (!mech || !Object.prototype.hasOwnProperty.call(VERBS, mech.command)) return false;
  if (mech.command === "insight" && (mech.args || [])[0] === "--list") return false;
  return true;
}

function describe(mech) {
  return VERBS[mech.command][1](mech.args || []) +
    "\n\nReply **yes** to proceed; anything else cancels.";
}

function confirmedArgs(mech) {
  const args = [...(mech.args || [])];
  return VERBS[mech.command][0] ? [...args, "--yes"] : args;
}

function isYes(text) {
  return /^\s*(y|yes|proceed)\.?\s*$/i.test(String(text || ""));
}

function pending(mech, now = Date.now()) {
  return { mech, at: now };
}

function expired(p, now = Date.now()) {
  return !p || now - p.at > TTL_MS;
}

/** [tool, args_summary] for the audit log, as bin/kb writes them. */
function auditOf(mech) {
  return [`kb ${mech.command}`, (mech.args || []).map(unq).join(" ")];
}

module.exports = { TTL_MS, needsConfirm, describe, confirmedArgs, isYes, pending, expired, auditOf };
