#!/usr/bin/env node
// versions.json — the only writer, and the gate.
//
// Obsidian reads versions.json (plugin version -> minimum Obsidian version) to
// decide which release an OLDER app may install: it picks the highest entry
// whose minimum the app satisfies, then downloads that tag's release assets
// from polleoai/athena. So every entry must be (a) a version that really
// exists as a public release, and (b) honest about what that release needs.
// Under-declare (b) and an older app is offered a build that throws; list a
// non-public version under (a) and the download 404s.
//
// Athena issue #230: every entry mapped to "1.0.0" and nothing past 1.8.3 was
// recorded, so Obsidian 1.0–1.4.3 was offered releases that call the
// standalone `setTooltip` (Obsidian 1.4.4+) unguarded while rendering the chat
// view. Nothing wrote this file — bumps touched only package.json and
// manifest.json — so it was hand-kept and stopped being kept.
//
// Entries are recorded at PUBLIC RELEASE time, not at bump time: auto-bump
// cuts a jivebug version per Gryphon release and most never ship publicly.
//
// Usage:
//   node scripts/versions-json.js --check
//       Floor rules only (offline). Safe on any commit.
//   node scripts/versions-json.js --check --release <tag> [--known-releases <file>]
//       Release gate: <tag> must equal manifest.version and map to
//       manifest.minAppVersion. With --known-releases (one public tag per
//       line), every OTHER entry must be a real public release.
//   node scripts/versions-json.js --record [<version>]
//       Record <version> (default manifest.version) -> manifest.minAppVersion.

"use strict";
const fs = require("fs");
const path = require("path");

// Minimum Obsidian version every release AT OR AFTER `since` needs. Measured
// against each tag's shipped main.js, not assumed: 1.0.0–1.2.3 do not import
// setTooltip; 1.2.4 onward call it unguarded. Raise or add a row whenever the
// bundle starts depending on a newer Obsidian API.
const FLOORS = [
  { since: "1.2.4", minApp: "1.4.4", why: "standalone setTooltip, called unguarded in the chat view" },
];

const cmp = (a, b) => {
  const pa = a.split("-")[0].split(".").map(Number), pb = b.split("-")[0].split(".").map(Number);
  for (let i = 0; i < 3; i++) if ((pa[i] || 0) !== (pb[i] || 0)) return (pa[i] || 0) - (pb[i] || 0);
  return 0;
};
const floorFor = (v) => FLOORS.filter((f) => cmp(v, f.since) >= 0)
  .reduce((acc, f) => (!acc || cmp(f.minApp, acc.minApp) > 0 ? f : acc), null);

function check(versions, manifest, { release, knownReleases } = {}) {
  const errors = [];
  for (const [v, minApp] of Object.entries(versions)) {
    const f = floorFor(v);
    if (f && cmp(minApp, f.minApp) < 0) {
      errors.push(`${v} -> ${minApp} is below the ${f.minApp} floor for >=${f.since} (${f.why})`);
    }
  }
  const mf = floorFor(manifest.version);
  if (mf && cmp(manifest.minAppVersion, mf.minApp) < 0) {
    errors.push(`manifest.minAppVersion ${manifest.minAppVersion} is below the ${mf.minApp} floor for ${manifest.version} (${mf.why})`);
  }
  if (release) {
    if (release !== manifest.version) errors.push(`release tag ${release} != manifest.version ${manifest.version}`);
    if (!(release in versions)) errors.push(`${release} is not in versions.json — run: node scripts/versions-json.js --record`);
    else if (versions[release] !== manifest.minAppVersion) {
      errors.push(`${release} -> ${versions[release]} but manifest.minAppVersion is ${manifest.minAppVersion}`);
    }
  }
  if (knownReleases) {
    for (const v of Object.keys(versions)) {
      if (v !== release && !knownReleases.has(v)) errors.push(`${v} is in versions.json but is not a public release — Obsidian would 404 downloading it`);
    }
  }
  return errors;
}

module.exports = { FLOORS, check, floorFor, cmp };

if (require.main === module) {
  const root = path.resolve(__dirname, "..");
  const vPath = process.env.ATHENA_VERSIONS_JSON || path.join(root, "versions.json");
  const mPath = process.env.ATHENA_MANIFEST_JSON || path.join(root, "manifest.json");
  const argv = process.argv.slice(2);
  const opt = (name) => { const i = argv.indexOf(name); return i >= 0 ? argv[i + 1] : undefined; };
  const versions = JSON.parse(fs.readFileSync(vPath, "utf8"));
  const manifest = JSON.parse(fs.readFileSync(mPath, "utf8"));

  if (argv.includes("--record")) {
    const i = argv.indexOf("--record");
    const v = argv[i + 1] && !argv[i + 1].startsWith("--") ? argv[i + 1] : manifest.version;
    const prev = versions[v];
    versions[v] = manifest.minAppVersion;
    const sorted = Object.fromEntries(Object.entries(versions).sort(([a], [b]) => cmp(a, b)));
    const errs = check(sorted, manifest);
    if (errs.length) { errs.forEach((e) => console.error(`✗ ${e}`)); process.exit(1); }
    fs.writeFileSync(vPath, JSON.stringify(sorted, null, 2) + "\n");
    console.log(`versions.json: ${v} -> ${manifest.minAppVersion}${prev ? ` (was ${prev})` : " (new)"}`);
    process.exit(0);
  }
  if (argv.includes("--check")) {
    const kr = opt("--known-releases");
    const knownReleases = kr ? new Set(fs.readFileSync(kr, "utf8").split(/\s+/).filter(Boolean)) : undefined;
    const errs = check(versions, manifest, { release: opt("--release"), knownReleases });
    if (errs.length) { errs.forEach((e) => console.error(`✗ ${e}`)); process.exit(1); }
    console.log(`✓ versions.json: ${Object.keys(versions).length} entries consistent with the minAppVersion floors`);
    process.exit(0);
  }
  console.error("usage: versions-json.js --check [--release <tag> [--known-releases <file>]] | --record [<version>]");
  process.exit(2);
}
