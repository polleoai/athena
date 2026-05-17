const esbuild = require("esbuild");
const fs = require("fs");
const path = require("path");
const { execSync } = require("child_process");

const watch = process.argv.includes("--watch");

// Athena's bundle transitively imports Gryphon source from vendor/gryphon/.
// Gryphon has its own package.json and its own build; this script only
// builds the Athena bundle. To rebuild Gryphon's own plugin main.js,
// run `npm run build:gryphon` (delegates to vendor/gryphon/).
const shared = {
  bundle: true,
  platform: "node",
  format: "cjs",
  external: ["obsidian", "electron", "@electron/remote"],
  sourcemap: false,
  // Minify production builds only — watch mode keeps readable output for
  // stack traces during dev. Minification roughly halves the bundle size.
  minify: !watch,
  logLevel: "info",
};

const athenaBuild = {
  ...shared,
  entryPoints: ["src/athena/plugin.js"],
  outfile: ".obsidian/plugins/athena/main.js",
};

// Athena's chat UI is GryphonChatView, which emits .gryphon-* class names.
// The matching styles live in vendor/gryphon/styles.css and must be copied
// into the plugin dir alongside main.js so Obsidian loads them. Without this
// the messages container has no overflow-y: auto, so chat history is hidden.
const stylesSrc = "vendor/gryphon/styles.css";
const stylesDst = ".obsidian/plugins/athena/styles.css";

// manifest.json lives at the Athena project root (source of truth — what the
// auto-bump workflow reads to derive the next patch version) and is copied
// into the plugin dir at build time. Without this copy, Obsidian shows the
// stale manifest baked into the plugin dir on disk instead of the version
// the source repo records.
const manifestSrc = "manifest.json";
const manifestDst = ".obsidian/plugins/athena/manifest.json";

// Python synthesis sources. As of 1.0.9 the plugin bundle carries
// Athena's own .py source files inside its install dir so the JS plugin
// can spawn them without requiring the user to clone the full Athena
// vault. Community Plugins / BRAT users only need a local Python install
// + `pip install pydantic` for full synthesis to work.
//
// We ship bin/lib/ (Python modules) and bin/config/ (the canonical
// athena.default.json that bin/lib/config.py reads). We do NOT ship the
// top-level bash scripts (bin/kb, bin/kb-*) — they're POSIX-only and
// not invoked by the plugin's spawn paths.
const PY_SRC_DIRS = [
  ["bin/lib",    ".obsidian/plugins/athena/bin/lib"],
  ["bin/config", ".obsidian/plugins/athena/bin/config"],
];

function copyDirRecursive(src, dst) {
  fs.rmSync(dst, { recursive: true, force: true });
  fs.mkdirSync(dst, { recursive: true });
  for (const entry of fs.readdirSync(src, { withFileTypes: true })) {
    // Skip Python bytecode caches — they're machine-specific and bloat
    // the bundle. Python will regenerate them on first import in the
    // user's environment if needed.
    if (entry.name === "__pycache__") continue;
    const s = path.join(src, entry.name);
    const d = path.join(dst, entry.name);
    if (entry.isDirectory()) {
      copyDirRecursive(s, d);
    } else if (entry.isFile()) {
      fs.copyFileSync(s, d);
    }
  }
}

// Catch the v0.7 release-process bug: package.json was bumped to 0.7.0 but
// manifest.json stayed at 0.5.5, so Obsidian's plugin list kept showing the
// stale version. Fail-fast at build so the next bump can't silently drift.
function assertVersionsAligned() {
  const pkgVersion = JSON.parse(fs.readFileSync("package.json", "utf8")).version;
  const manifestVersion = JSON.parse(fs.readFileSync("manifest.json", "utf8")).version;
  if (pkgVersion !== manifestVersion) {
    console.error(
      `\n  ✗ Version drift detected:\n` +
      `    package.json:  ${pkgVersion}\n` +
      `    manifest.json: ${manifestVersion}\n` +
      `    Fix with: npm run bump-version <new-version>\n`
    );
    process.exit(1);
  }
}

async function run() {
  assertVersionsAligned();
  fs.mkdirSync(path.dirname(athenaBuild.outfile), { recursive: true });

  if (watch) {
    const ctx = await esbuild.context(athenaBuild);
    await ctx.watch();
    fs.copyFileSync(stylesSrc, stylesDst);
    fs.copyFileSync(manifestSrc, manifestDst);
    for (const [src, dst] of PY_SRC_DIRS) copyDirRecursive(src, dst);
    console.log(`Watching: ${athenaBuild.entryPoints[0]} → ${athenaBuild.outfile}`);
    console.log(`Copied: ${stylesSrc} → ${stylesDst}`);
    console.log(`Copied: ${manifestSrc} → ${manifestDst}`);
    for (const [src, dst] of PY_SRC_DIRS) console.log(`Copied: ${src}/ → ${dst}/`);
  } else {
    await esbuild.build(athenaBuild);
    const size = fs.statSync(athenaBuild.outfile).size;
    console.log(`Built ${athenaBuild.outfile} — ${(size / 1024).toFixed(1)} kb`);
    fs.copyFileSync(stylesSrc, stylesDst);
    fs.copyFileSync(manifestSrc, manifestDst);
    for (const [src, dst] of PY_SRC_DIRS) copyDirRecursive(src, dst);
    console.log(`Copied ${stylesSrc} → ${stylesDst}`);
    console.log(`Copied ${manifestSrc} → ${manifestDst}`);
    for (const [src, dst] of PY_SRC_DIRS) console.log(`Copied ${src}/ → ${dst}/`);
  }
}

run().catch((e) => {
  console.error(e);
  process.exit(1);
});
