#!/usr/bin/env node
"use strict";
/**
 * pro-rehearse.js: the matrix run up to, but not including, the first VM
 * boot. pro-matrix-payload.sh runs it when PAYLOAD_STOP_BEFORE_BOOT=1 (PGM,
 * 11-21: runs -1 and -2 each died on a Max-only assumption, one Pro slot at a
 * time; a rehearsal is a light row with no VM).
 *
 *   node pro-rehearse.js <argus-dir> <bundle-dir> <report-dir> <utmctl>
 *   (cwd: scripts/vm-tests, ARGUS_REPO set as vm-run would get it)
 *
 * It uses argus's OWN pre-boot code, the same functions vm-run calls first:
 *   1. load argus.config.js (its Pro-only guard, ARGUS_REPO, every path)
 *   2. runBuild with build=false: vm-run --no-build's artifact check
 *   3. bundle(config.bundle), written to <bundle-dir> instead of /tmp
 * then checks what the VMs would get, and that `<utmctl> list` (read-only)
 * names every target's VM. Nothing is booted, served or installed.
 * Writes <report-dir>/rehearsal.json; exit 0 = everything before boot passed.
 */
const fs = require("fs");
const os = require("os");
const path = require("path");
const { builtinModules } = require("module");
const { execFileSync } = require("child_process");

const TARGETS = ["linux-vm", "windows-vm"];
const MUST_SHIP = [
  "argus.config.js", "package.json", "playwright.config.js",
  "argus/package.json", "argus/src/host/vm-run-side.js",
  ".obsidian/plugins/athena/main.js", ".obsidian/plugins/gryphon/main.js",
];

// Where the import-closure walk starts: every bundled spec, plus what a spec
// loads through a computed path the walk can't read (07-guardrail requires
// <GRYPHON_PROTECT_DIR>/src/attack-detector.js, which Playwright maps to .ts).
const CLOSURE_ENTRIES = ["vendor/gryphon/packages/protect/src/attack-detector.ts"];
// Resolved in the guest without the bundle: `obsidian` is stubbed by 07.
const CLOSURE_STUBS = ["obsidian"];

const BUILTIN = new Set(builtinModules.flatMap((m) => [m, `node:${m}`]));
const IMPORT_RE = /(?:^|[^.\w$])(?:require\(\s*|from\s+|import\s*\(\s*|import\s+)["']([^"']+)["']/g;

// The Pro's APFS ignores case and the Debian guest's ext4 doesn't: a path
// counts only if every segment below `root` matches its directory listing
// exactly, as it must in the guest.
function exactCase(p, root) {
  let d = root;
  for (const seg of path.relative(root, p).split(path.sep)) {
    if (seg === "" || seg === ".") continue;
    let names;
    try { names = fs.readdirSync(d); } catch { return false; }
    if (!names.includes(seg)) return false;
    d = path.join(d, seg);
  }
  return true;
}

function asFile(p, root) {
  const bare = p.replace(/\.js$/, "");
  for (const c of [p, `${bare}.ts`, `${bare}.js`, `${p}.ts`, `${p}.js`, `${p}.cjs`, `${p}.json`,
                   path.join(p, "index.ts"), path.join(p, "index.js")]) {
    try { if (fs.statSync(c).isFile() && exactCase(c, root)) return c; } catch { /* next */ }
  }
  return null;
}

// Comments out, string literals kept, so a "/*" inside a string (a glob, say)
// can't swallow the code after it.
function stripComments(src) {
  let out = "", i = 0;
  while (i < src.length) {
    const c = src[i], n = src[i + 1];
    if (c === "/" && n === "*") { const e = src.indexOf("*/", i + 2); i = e < 0 ? src.length : e + 2; out += " "; continue; }
    if (c === "/" && n === "/") { const e = src.indexOf("\n", i); i = e < 0 ? src.length : e; continue; }
    if (c === '"' || c === "'" || c === "`") {
      let j = i + 1;
      while (j < src.length && src[j] !== c && !(c !== "`" && src[j] === "\n")) j += src[j] === "\\" ? 2 : 1;
      out += src.slice(i, j + 1); i = j + 1; continue;
    }
    out += c; i++;
  }
  return out;
}

// Node's lookup, but bounded to `root`: only node_modules dirs at or below the
// bundle root count, so NODE_PATH, ~/.node_modules or a parent dir's
// node_modules can't make a missing package look shipped.
function resolveBare(spec, fromDir, root) {
  const parts = spec.split("/");
  const name = spec.startsWith("@") ? parts.slice(0, 2).join("/") : parts[0];
  const sub = parts.slice(spec.startsWith("@") ? 2 : 1).join("/");
  for (let d = fromDir; ; d = path.dirname(d)) {
    const pkgDir = path.join(d, "node_modules", name);
    const pj = path.join(pkgDir, "package.json");
    if (fs.existsSync(pj)) { // case is checked on the file it resolves to (asFile)
      const pkg = JSON.parse(fs.readFileSync(pj, "utf8"));
      if (pkg.exports) {
        let t = pkg.exports[sub ? `./${sub}` : "."];
        if (t === undefined && !sub && typeof pkg.exports === "string") t = pkg.exports;
        while (t && typeof t === "object") t = t.require || t.node || t.default;
        return typeof t === "string" ? asFile(path.join(pkgDir, t), root) : null;
      }
      return asFile(path.join(pkgDir, sub || pkg.main || "index.js"), root);
    }
    if (d === root || path.dirname(d) === d) return null;
  }
}

// Every import reachable from `entries` in the extracted bundle at `root`.
// `provided` are packages the guest installs itself (package.vm.json), so the
// walk stops there. Returns { files, missing: [{ spec, from }] }. Type-only
// imports are dropped; other imports used only as types are kept, which can
// only over-ship.
function importClosure(root, entries, provided) {
  const seen = new Set(), missing = [];
  const stack = entries.map((e) => asFile(path.join(root, e), root) || { lost: e });
  while (stack.length) {
    const f = stack.pop();
    if (f && f.lost) { missing.push({ spec: f.lost, from: "(entry)" }); continue; }
    if (seen.has(f) || f.endsWith(".json") || f.endsWith(".node")) { seen.add(f); continue; }
    seen.add(f);
    // Type-only imports/re-exports vanish at runtime. The match must end at
    // its own `from "..."` and can't cross a `;` or `=` (a type alias), so it
    // never swallows the import after it.
    const src = stripComments(fs.readFileSync(f, "utf8"))
      .replace(/^\s*(?:import|export)\s+type\s[^;=]*?from\s*["'][^"']+["']/gm, "");
    for (const m of src.matchAll(IMPORT_RE)) {
      const spec = m[1], from = path.relative(root, f);
      if (spec.startsWith(".") || spec.startsWith("/")) {
        const r = asFile(path.resolve(path.dirname(f), spec), root);
        if (r) stack.push(r); else missing.push({ spec, from });
        continue;
      }
      const name = spec.startsWith("@") ? spec.split("/").slice(0, 2).join("/") : spec.split("/")[0];
      if (BUILTIN.has(spec) || BUILTIN.has(name) || CLOSURE_STUBS.includes(name) || provided.includes(name)) continue;
      const r = resolveBare(spec, path.dirname(f), root);
      if (r) stack.push(r); else missing.push({ spec, from });
    }
  }
  return { files: [...seen].map((f) => path.relative(root, f)).sort(), missing };
}

function rehearse(argusDir, bundleDir, utmctl) {
  const checks = [];
  const check = (name, ok, detail) => { checks.push({ name, ok: !!ok, detail: detail || "" }); return ok; };

  const config = require(path.resolve("argus.config.js"));
  const { runBuild } = require(path.join(argusDir, "src", "host", "vm-run.js"));
  const { bundle } = require(path.join(argusDir, "src", "host", "bundle.js"));

  try { runBuild(config, { build: false, cwd: process.cwd(), logger: false }, () => {}); check("artifacts (vm-run --no-build preflight)", true); }
  catch (e) { check("artifacts (vm-run --no-build preflight)", false, e.message); }

  return bundle({ ...config.bundle, out: path.join(bundleDir, "rehearsal-bundle.tar.gz") }).then((b) => {
    check("bundle built", true, `${b.bytes} bytes, ${b.entries.length} entries, sha256 ${b.sha256.slice(0, 12)}`);
    for (const f of MUST_SHIP) check(`bundle ships ${f}`, b.entries.includes(f));
    const junk = b.entries.filter((e) => /^argus\/(node_modules|\.git)\//.test(e));
    check("bundle has no argus/node_modules or argus/.git", junk.length === 0, junk.slice(0, 3).join(", "));
    const argusSrc = (config.bundle.include.find((e) => e.dest === "argus/") || {}).src;
    check("bundled argus is ARGUS_REPO", argusSrc && argusSrc === path.resolve(process.env.ARGUS_REPO || ""), argusSrc);

    // Every import the specs reach must resolve inside the bundle as the
    // guest extracts it (matrix -3: protect's workspace import resolved on the
    // Max only, through a symlink the bundler skips).
    const x = fs.mkdtempSync(path.join(os.tmpdir(), "rehearse-bundle-"));
    try {
      execFileSync("tar", ["-xzf", b.out, "-C", x]);
      const vmPkg = JSON.parse(fs.readFileSync(path.join(x, "package.json"), "utf8"));
      const provided = Object.keys(vmPkg.dependencies || {});
      const specs = b.entries.filter((e) => /^specs\/[^/]+\.js$/.test(e));
      const c = importClosure(x, specs.concat(CLOSURE_ENTRIES), provided);
      check("every spec import resolves in the bundle", c.missing.length === 0,
        c.missing.length ? c.missing.slice(0, 5).map((m) => `${m.spec} from ${m.from}`).join("; ")
          : `${c.files.length} files, guest-installed: ${provided.join(", ")}`);
    } catch (e) {
      check("every spec import resolves in the bundle", false, e.message);
    } finally {
      fs.rmSync(x, { recursive: true, force: true });
    }

    let list = "";
    try { list = execFileSync(utmctl, ["list"], { encoding: "utf8" }); check("utmctl list", true); }
    catch (e) { check("utmctl list", false, e.message); }
    for (const t of TARGETS) {
      const vm = config.targets && config.targets[t] && config.targets[t].provision && config.targets[t].provision.vm;
      check(`${t}: VM "${vm}" exists`, vm && list.split("\n").some((l) => l.trimEnd().endsWith(" " + vm)));
    }
    return checks;
  }, (e) => { check("bundle built", false, e.message); return checks; });
}

if (require.main === module) {
  const [argusDir, bundleDir, reportDir, utmctl] = process.argv.slice(2);
  if (!argusDir || !bundleDir || !reportDir || !utmctl) { console.error("usage: pro-rehearse.js <argus-dir> <bundle-dir> <report-dir> <utmctl>"); process.exit(2); }
  rehearse(argusDir, bundleDir, utmctl).then((checks) => {
    const ok = checks.every((c) => c.ok);
    fs.writeFileSync(path.join(reportDir, "rehearsal.json"), JSON.stringify({ ok, checks }, null, 2) + "\n");
    for (const c of checks) console.log(`${c.ok ? "ok  " : "FAIL"} ${c.name}${c.detail ? ` (${c.detail})` : ""}`);
    console.log(ok ? "REHEARSAL: PASS (stopped before the first VM boot)" : "REHEARSAL: FAIL");
    process.exit(ok ? 0 : 1);
  }, (e) => { console.error(`REHEARSAL: FAIL ${e.stack || e}`); process.exit(1); });
}

module.exports = { rehearse, importClosure, MUST_SHIP, CLOSURE_ENTRIES };
