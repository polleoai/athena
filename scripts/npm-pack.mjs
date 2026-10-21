#!/usr/bin/env node
// npm-pack.mjs — pack @polleoai/athena for the registry.
//
// Shared by the release session and the Release workflow (npm-build job), so
// both produce the same package. Mirrors Gryphon's .github/scripts/npm-pack.mjs.
//
//   node scripts/npm-pack.mjs --out <dir> [--root <tree>] [--manifest-out <file>]
//   node scripts/npm-pack.mjs --out <dir> [--root <tree>] --verify <record> --version <v>
//
// The package is the built plugin plus its full source: main.js, manifest.json
// and styles.css (what Obsidian installs; must already be built), the Python
// backend (bin/), the MCP server (server/), the plugin source (src/), the web
// clipper templates (assets/) and the deep-capture script. Nothing else: no
// tests, no dev tooling, no vault content. This file is the only definition of
// the package — the repo's package.json stays private and is not published.
//
// No runtime dependencies are declared: the one npm dependency
// (@anthropic-ai/sdk) is bundled into main.js at build time.
//
// --manifest-out records every file with its sha256. --verify recomputes that
// record for this build and fails unless it is identical, so the workflow only
// stages what the release session built and checked. Files are compared
// unpacked, so differences in compression can't cause a mismatch.

import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const NAME = "@polleoai/athena";
// Paths relative to the tree root. Directories are copied recursively.
const FILES = [
  "main.js", "manifest.json", "styles.css", "versions.json",
  "README.md", "LICENSE", "CHANGELOG.md",
  "bin", "server", "src", "assets", "scripts/capture-deep.js",
];
// Never shipped, wherever they appear inside FILES.
const SKIP = (name) => name === "__pycache__" || name === ".DS_Store" || name.endsWith(".pyc") || name === "node_modules";
// A path matching any of these means the tree is wrong (a vault, not the repo).
const FORBIDDEN = /(^|\/)(wiki|raw|inbox|clippings|\.obsidian|\.kb-trash|\.git|\.env[^/]*|\.mcp\.json|RULES\.md|AGENTS\.md)(\/|$)/;

const SCRIPT_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

function args(argv) {
  const out = {};
  for (let i = 0; i < argv.length; i += 2) {
    if (!argv[i].startsWith("--") || argv[i + 1] === undefined) throw new Error(`bad argument: ${argv[i]}`);
    const key = argv[i].slice(2);
    if (!["out", "root", "manifest-out", "verify", "version"].includes(key)) throw new Error(`unknown option --${key}`);
    out[key] = argv[i + 1];
  }
  return out;
}

const sha256 = (file) => createHash("sha256").update(fs.readFileSync(file)).digest("hex");

function fileMap(dir) {
  const files = {};
  const walk = (d) => {
    for (const e of fs.readdirSync(d, { withFileTypes: true })) {
      const p = path.join(d, e.name);
      if (e.isDirectory()) walk(p);
      else files[path.relative(dir, p).split(path.sep).join("/")] = sha256(p);
    }
  };
  walk(dir);
  return Object.fromEntries(Object.entries(files).sort(([a], [b]) => (a < b ? -1 : 1)));
}

function copyInto(root, rel, stage) {
  const src = path.join(root, rel);
  if (!fs.existsSync(src)) throw new Error(`${rel} is missing from ${root}${["main.js", "styles.css"].includes(rel) ? " — build first (npm run build)" : ""}`);
  const dst = path.join(stage, rel);
  if (fs.statSync(src).isDirectory()) {
    fs.cpSync(src, dst, { recursive: true, filter: (s) => !SKIP(path.basename(s)) });
  } else {
    fs.mkdirSync(path.dirname(dst), { recursive: true });
    fs.copyFileSync(src, dst);
  }
}

function packageJson(root) {
  const manifest = JSON.parse(fs.readFileSync(path.join(root, "manifest.json"), "utf8"));
  const dev = JSON.parse(fs.readFileSync(path.join(root, "package.json"), "utf8"));
  if (dev.version !== manifest.version) throw new Error(`package.json ${dev.version} != manifest.json ${manifest.version}`);
  return {
    name: NAME,
    version: manifest.version,
    description: manifest.description,
    keywords: ["obsidian", "obsidian-plugin", "knowledge-base", "second-brain", "llm"],
    homepage: "https://github.com/polleoai/athena",
    bugs: { url: "https://github.com/polleoai/athena/issues" },
    repository: { type: "git", url: "git+https://github.com/polleoai/athena.git" },
    license: "MIT",
    author: manifest.author,
    publishConfig: { access: "public" },
  };
}

function pack(root, outDir) {
  fs.mkdirSync(outDir, { recursive: true });
  const stage = fs.mkdtempSync(path.join(os.tmpdir(), "athena-npm-pack-"));
  try {
    const pkgDir = path.join(stage, "package");
    for (const rel of FILES) copyInto(root, rel, pkgDir);
    const m = packageJson(root);
    fs.writeFileSync(path.join(pkgDir, "package.json"), JSON.stringify(m, null, 2) + "\n");
    const files = fileMap(pkgDir);
    const bad = Object.keys(files).filter((f) => FORBIDDEN.test(f));
    if (bad.length) throw new Error(`refusing to pack — forbidden paths:\n  ${bad.slice(0, 20).join("\n  ")}`);
    // --ignore-scripts: nothing in the staged package should run at pack time.
    execFileSync("npm", ["pack", pkgDir, "--ignore-scripts", "--pack-destination", outDir, "--silent"], { cwd: stage, stdio: ["ignore", "ignore", "inherit"] });
    return { packages: { [m.name]: { version: m.version, files } } };
  } finally {
    fs.rmSync(stage, { recursive: true, force: true });
  }
}

function verify(record, recordFile, version) {
  const want = JSON.parse(fs.readFileSync(recordFile, "utf8"));
  const errs = [];
  const names = (o) => Object.keys(o.packages || {}).sort().join(",");
  if (names(want) !== names(record)) errs.push(`package set differs: built [${names(record)}], record [${names(want)}]`);
  for (const [name, got] of Object.entries(record.packages)) {
    const exp = want.packages && want.packages[name];
    if (!exp) continue;
    if (got.version !== version) errs.push(`${name}: built version ${got.version}, release is ${version}`);
    if (exp.version !== version) errs.push(`${name}: record version ${exp.version}, release is ${version}`);
    for (const f of new Set([...Object.keys(got.files), ...Object.keys(exp.files)])) {
      if (!(f in exp.files)) errs.push(`${name}: ${f} is in this build but not in the record`);
      else if (!(f in got.files)) errs.push(`${name}: ${f} is in the record but not in this build`);
      else if (got.files[f] !== exp.files[f]) errs.push(`${name}: ${f} differs from the record`);
    }
  }
  return errs;
}

const a = args(process.argv.slice(2));
if (!a.out) throw new Error("--out <dir> is required");
const root = path.resolve(a.root || SCRIPT_ROOT);
const record = pack(root, path.resolve(a.out));
const [[name, { version, files }]] = Object.entries(record.packages);
console.log(`npm-pack: ${name}@${version} — ${Object.keys(files).length} files`);
if (a["manifest-out"]) fs.writeFileSync(a["manifest-out"], JSON.stringify(record, null, 2) + "\n");
if (a.verify) {
  if (!a.version) throw new Error("--verify needs --version <release version>");
  const errs = verify(record, a.verify, a.version);
  if (errs.length) {
    console.error(`npm-pack: build does NOT match ${a.verify} — refusing to stage:`);
    for (const e of errs.slice(0, 50)) console.error(`  - ${e}`);
    process.exit(1);
  }
  console.log(`npm-pack: ${name} matches ${a.verify} file for file (${a.version})`);
}
