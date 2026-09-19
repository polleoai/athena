// sync-gryphon-pin.js — write <submodule>.pin from the submodule's own pin.
//
// The pin file exists because `ensure-gryphon.js` must know WHICH Gryphon to
// fetch in an environment with no git metadata (the Obsidian plugin review
// builds from a source archive). git records the submodule commit in the tree,
// which is unreadable there, so it is mirrored into a plain file.
//
// That mirroring is a second place the Gryphon version is written down, so it
// is GENERATED, never hand-edited: run this after any submodule bump, and the
// release procedure runs `--check` before cutting. `--check` exits non-zero
// when the file disagrees with the submodule.
const fs = require("fs");
const path = require("path");
const { execSync } = require("child_process");

function git(cmd) {
  return execSync(cmd, { encoding: "utf8", stdio: ["ignore", "pipe", "pipe"] });
}

// Resolve the submodule path AS THE REPO RECORDS IT, from .gitmodules — never
// by building it from the cwd. `git ls-tree` matches pathspecs relative to the
// working directory, and on a case-insensitive filesystem a cwd of
// `projects/athena` does not match an index entry of `Projects/athena`: the
// lookup returns empty and a gate written the naive way passes over a pin it
// never read. .gitmodules records the path relative to the repo root in the
// repo's own spelling, which is the only trustworthy source here.
function gryphonSubmodulePath() {
  const out = git('git config --file .gitmodules --get-regexp "^submodule\\..*\\.path$"');
  for (const line of out.split("\n")) {
    const value = line.split(/\s+/).slice(1).join(" ").trim();
    if (value.endsWith("vendor/gryphon")) return value;
  }
  throw new Error("no vendor/gryphon entry in .gitmodules");
}

function resolve() {
  const root = git("git rev-parse --show-toplevel").trim();
  process.chdir(root); // .gitmodules and --full-tree are both root-relative
  const subPath = gryphonSubmodulePath();
  const out = git(`git ls-tree --full-tree HEAD -- "${subPath}"`);
  const m = out.match(/^160000 commit ([0-9a-f]{40})\t/);
  if (!m) throw new Error(`no gitlink for ${subPath}: ${out.trim() || "(empty)"}`);
  // The pin sits beside the submodule, so it travels with the athena tree
  // whether that tree is at the repo root or nested under Projects/.
  return { pin: m[1], pinFile: `${subPath}.pin`, root };
}

let info;
try {
  info = resolve();
} catch (err) {
  console.error(
    "[sync-gryphon-pin] could not read the submodule pin from git.\n" +
    `  ${err.message}\n` +
    "  This script maintains the pin file from a git checkout; a source archive\n" +
    "  already carries the committed pin and needs nothing."
  );
  process.exit(2);
}

const { pin, pinFile } = info;

if (process.argv.includes("--check")) {
  const have = fs.existsSync(pinFile) ? fs.readFileSync(pinFile, "utf8").trim() : "";
  if (have !== pin) {
    console.error(
      `[sync-gryphon-pin] ${pinFile} is stale.\n` +
      `  file:      ${have || "(missing)"}\n` +
      `  submodule: ${pin}\n` +
      `  Run: node scripts/sync-gryphon-pin.js`
    );
    process.exit(1);
  }
  console.log(`[sync-gryphon-pin] ${pinFile} matches the submodule (${pin.slice(0, 12)}).`);
  process.exit(0);
}

fs.mkdirSync(path.dirname(pinFile), { recursive: true });
fs.writeFileSync(pinFile, `${pin}\n`);
console.log(`[sync-gryphon-pin] wrote ${pinFile} = ${pin}`);
