#!/usr/bin/env node
// release-notes.js — keep every tagged version's release notes from going missing.
//
//   release-notes.js --check <version> [--root DIR]
//   release-notes.js --write <version> --gryphon <G> --date YYYY-MM-DD [--root DIR]
//
// The workspace rule: every release ships docs/announcements/<version>.md
// ("backfill is allowed; gaps are not"), and the CHANGELOG has its entry. The
// Gryphon auto-bump tagged 1.8.19 and 1.8.20 with neither, because nothing on
// the bump-and-tag path wrote or checked them. auto-bump-gryphon.yml now runs
// --write and commits the result; tag-on-bump-merge.yml runs --check and
// refuses to tag on a gap.
//
// --write never overwrites an announcement that exists and never adds a second
// CHANGELOG entry, so it is safe to rerun and never clobbers hand-written copy.
// Exit: 0 ok, 1 a gap (--check), 2 bad usage or input.
"use strict";

const fs = require("fs");
const path = require("path");

const SEMVER = /^\d+\.\d+\.\d+$/;
const DATE = /^\d{4}-\d{2}-\d{2}$/;

function usage(msg) {
  process.stderr.write(`release-notes: ${msg}\n` +
    "usage: release-notes.js --check <version> [--root DIR]\n" +
    "       release-notes.js --write <version> --gryphon <G> --date YYYY-MM-DD [--root DIR]\n");
  process.exit(2);
}

function parse(argv) {
  const o = {};
  for (let i = 0; i < argv.length; i += 2) {
    const k = argv[i];
    if (!["--check", "--write", "--gryphon", "--date", "--root"].includes(k)) usage(`unknown argument ${k}`);
    if (argv[i + 1] === undefined) usage(`${k} needs a value`);
    o[k.slice(2)] = argv[i + 1];
  }
  const mode = o.check !== undefined ? "check" : o.write !== undefined ? "write" : null;
  if (!mode || (o.check !== undefined && o.write !== undefined)) usage("give one of --check or --write");
  o.mode = mode;
  o.version = o[mode];
  if (!SEMVER.test(o.version)) usage(`version must be N.N.N, got '${o.version}'`);
  if (mode === "write") {
    if (!SEMVER.test(o.gryphon || "")) usage(`--gryphon must be N.N.N, got '${o.gryphon}'`);
    if (!DATE.test(o.date || "")) usage(`--date must be YYYY-MM-DD, got '${o.date}'`);
  }
  o.root = path.resolve(o.root || path.join(__dirname, ".."));
  return o;
}

const annPath = (root, v) => path.join(root, "docs", "announcements", `${v}.md`);
const clPath = (root) => path.join(root, "CHANGELOG.md");
const tplPath = (root) => path.join(root, "docs", "announcements", "TEMPLATE.md");
const isFile = (p) => { try { return fs.statSync(p).isFile(); } catch { return false; } };

// Release headings ("## [1.8.20] — 2026-11-20") outside fenced code blocks, in
// file order, with their line offsets. A heading quoted inside ``` is not an
// entry, and a heading needs its date to count.
function releaseHeadings(cl) {
  const out = [];
  let fenced = false, offset = 0;
  for (const line of cl.split("\n")) {
    if (/^\s*(```|~~~)/.test(line)) fenced = !fenced;
    const m = !fenced && /^## \[(\d+\.\d+\.\d+)\] — \d{4}-\d{2}-\d{2}\b/.exec(line);
    if (m) out.push({ version: m[1], offset });
    offset += line.length + 1;
  }
  return out;
}
const hasEntry = (cl, v) => releaseHeadings(cl).some((h) => h.version === v);
const cmp = (a, b) => {
  const x = a.split(".").map(Number), y = b.split(".").map(Number);
  return x[0] - y[0] || x[1] - y[1] || x[2] - y[2];
};

function announcementGap(root, v) {
  const a = annPath(root, v);
  if (!isFile(a)) return `docs/announcements/${v}.md is missing`;
  const first = fs.readFileSync(a, "utf8").split("\n")[0];
  if (!first.startsWith(`# Athena v${v}`)) {
    return `docs/announcements/${v}.md does not start with "# Athena v${v}" (empty or not an announcement)`;
  }
  return null;
}

function gaps(root, v) {
  const out = [];
  const a = announcementGap(root, v);
  if (a) out.push(a);
  const cl = isFile(clPath(root)) ? fs.readFileSync(clPath(root), "utf8") : "";
  if (!hasEntry(cl, v)) out.push(`CHANGELOG.md has no "## [${v}] — YYYY-MM-DD" entry`);
  return out;
}

function stub(root, v, g, date) {
  const tpl = fs.readFileSync(tplPath(root), "utf8");
  const lines = tpl.replace(/\{VERSION\}/g, v).split("\n");
  const banner = [
    "",
    `> **STUB — not released publicly.** Tag \`${v}\` was cut on the dev repo by the Gryphon ${g} ` +
      "auto-bump. A public release is the user's decision; fill in the copy below before posting.",
  ];
  const body = lines.slice(1).join("\n")
    .replace(/^Released YYYY-MM-DD\.[^\n]*/m, `Released: not yet (dev tag ${date}).`)
    .replace(/^<one sentence — what changed[^\n]*/m, `Athena ${v} bundles Gryphon ${g}.`);
  return [lines[0], ...banner, body].join("\n");
}

function entry(v, g, date) {
  return `## [${v}] — ${date}\n\n` +
    `Headline: **Athena now bundles Gryphon ${g}.** An automated update of the built-in assistant; ` +
    "not released publicly yet.\n\n" +
    "### Changed\n\n" +
    `- **The built-in assistant is Gryphon ${g}.** See Gryphon's release notes for what changed.\n\n`;
}

function write(o) {
  // Check everything first, so a failure writes nothing (no stub left behind
  // next to a CHANGELOG that could not be updated).
  const a = annPath(o.root, o.version);
  const needStub = !fs.existsSync(a);
  if (!isFile(clPath(o.root))) usage(`no CHANGELOG.md in ${o.root}`);
  if (needStub && !isFile(tplPath(o.root))) usage(`no docs/announcements/TEMPLATE.md in ${o.root}`);
  if (needStub) {
    fs.writeFileSync(a, stub(o.root, o.version, o.gryphon, o.date));
    console.log(`wrote docs/announcements/${o.version}.md (stub)`);
  }
  const cl = fs.readFileSync(clPath(o.root), "utf8");
  if (!hasEntry(cl, o.version)) {
    // Newest release first: before the first release heading with a LOWER
    // version, so [Unreleased] stays on top and a backfill of an older version
    // lands in its place, not above newer ones.
    const lower = releaseHeadings(cl).find((h) => cmp(h.version, o.version) < 0);
    const at = lower ? lower.offset : cl.length;
    const sep = at === cl.length && !cl.endsWith("\n\n") ? (cl.endsWith("\n") ? "\n" : "\n\n") : "";
    fs.writeFileSync(clPath(o.root), cl.slice(0, at) + sep + entry(o.version, o.gryphon, o.date) + cl.slice(at));
    console.log(`added CHANGELOG.md [${o.version}]`);
  }
}

function main() {
  const o = parse(process.argv.slice(2));
  if (o.mode === "write") write(o);
  const g = gaps(o.root, o.version);
  if (g.length) {
    process.stderr.write(`release-notes: ${o.version} has gaps (every release ships its notes):\n` +
      g.map((x) => `  - ${x}\n`).join(""));
    process.exit(1);
  }
  if (o.mode === "check") console.log(`release-notes: ${o.version} ok`);
}

main();
