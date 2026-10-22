/**
 * test-versions-json.js — node --test
 *
 * Regression guard for athena#230. Obsidian chooses what an OLDER app may
 * install from versions.json (highest entry whose minimum the app meets). Every
 * entry said "1.0.0" and nothing past 1.8.3 was recorded, so Obsidian 1.0–1.4.3
 * was offered releases that call the standalone setTooltip (1.4.4+) unguarded.
 *
 * Invariants: no entry at or after a floor's `since` declares below that floor;
 * releases before it keep their honest lower minimum; a release is gated on its
 * own entry matching manifest.minAppVersion; every other entry must be a real
 * public release. Set ATHENA_VERSIONS_SRC to run the repo-file case against a
 * pre-fix versions.json and confirm this test FAILS on the defect.
 */
"use strict";
const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("fs");
const os = require("os");
const path = require("path");
const { execFileSync } = require("child_process");

const REPO = path.resolve(__dirname, "..");
const SCRIPT = path.join(REPO, "scripts/versions-json.js");
const { check } = require(SCRIPT);

function run(args, versions, manifest) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "athena-vj-"));
  const v = path.join(dir, "versions.json"), m = path.join(dir, "manifest.json");
  fs.writeFileSync(v, JSON.stringify(versions));
  fs.writeFileSync(m, JSON.stringify(manifest));
  try {
    const out = execFileSync("node", [SCRIPT, ...args], {
      env: { ...process.env, ATHENA_VERSIONS_JSON: v, ATHENA_MANIFEST_JSON: m }, encoding: "utf8", stdio: "pipe" });
    return { code: 0, out, versions: JSON.parse(fs.readFileSync(v, "utf8")), dir };
  } catch (e) {
    return { code: e.status, out: String(e.stderr), versions: JSON.parse(fs.readFileSync(v, "utf8")), dir };
  }
}
const MANIFEST = { version: "1.8.12", minAppVersion: "1.4.4" };

test("the repo's versions.json satisfies the floors", () => {
  const src = process.env.ATHENA_VERSIONS_SRC || path.join(REPO, "versions.json");
  const errs = check(JSON.parse(fs.readFileSync(src, "utf8")),
    JSON.parse(fs.readFileSync(path.join(REPO, "manifest.json"), "utf8")));
  assert.deepEqual(errs, []);
});

test("#230 repro: 1.2.4+ under-declared is rejected; pre-setTooltip 1.x keeps 1.0.0", () => {
  const errs = check({ "1.1.1": "1.0.0", "1.2.4": "1.0.0", "1.8.3": "1.0.0" }, MANIFEST);
  assert.equal(errs.length, 2);
  assert.ok(errs.some((e) => e.startsWith("1.2.4 -> 1.0.0")));
  assert.ok(errs.some((e) => e.startsWith("1.8.3 -> 1.0.0")));
  assert.ok(!errs.some((e) => e.startsWith("1.1.1")), "1.1.1 ships no setTooltip; 1.0.0 is honest");
});

test("a release is gated on its own entry", () => {
  const r = run(["--check", "--release", "1.8.12"], { "1.8.1": "1.4.4" }, MANIFEST);
  assert.equal(r.code, 1);
  assert.match(r.out, /1\.8\.12 is not in versions\.json/);
});

test("--record writes manifest.minAppVersion, and the release gate then passes", () => {
  const r = run(["--record"], { "1.8.1": "1.4.4" }, MANIFEST);
  assert.equal(r.code, 0, r.out);
  assert.equal(r.versions["1.8.12"], "1.4.4");
  const vfile = path.join(r.dir, "versions.json");
  const out = execFileSync("node", [SCRIPT, "--check", "--release", "1.8.12"], {
    env: { ...process.env, ATHENA_VERSIONS_JSON: vfile, ATHENA_MANIFEST_JSON: path.join(r.dir, "manifest.json") },
    encoding: "utf8" });
  assert.match(out, /✓/);
});

test("--record refuses a manifest whose minAppVersion is below the floor", () => {
  const r = run(["--record"], { "1.8.1": "1.4.4" }, { version: "1.8.12", minAppVersion: "1.0.0" });
  assert.equal(r.code, 1);
  assert.equal(r.versions["1.8.12"], undefined, "nothing written on refusal");
});

test("an entry that is not a public release is rejected", () => {
  const errs = check({ "1.8.1": "1.4.4", "1.8.3": "1.4.4", "1.8.12": "1.4.4" }, MANIFEST,
    { release: "1.8.12", knownReleases: new Set(["1.8.1"]) });
  assert.deepEqual(errs, ["1.8.3 is in versions.json but is not a public release — Obsidian would 404 downloading it"]);
});

test("the shipped bundle still needs the floor its manifest declares", () => {
  const bundle = path.join(REPO, ".obsidian/plugins/athena/main.js");
  if (!fs.existsSync(bundle)) return;
  if (/setTooltip:/.test(fs.readFileSync(bundle, "utf8"))) {
    const m = JSON.parse(fs.readFileSync(path.join(REPO, "manifest.json"), "utf8"));
    assert.deepEqual(check({}, m), [], "bundle imports setTooltip; manifest.minAppVersion must be >= 1.4.4");
  }
});
