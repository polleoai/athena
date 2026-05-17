#!/usr/bin/env node
// Bump package.json + manifest.json in lockstep.
//
// Why this exists: shipping v0.7 left manifest.json at 0.5.5, so Obsidian
// kept showing the stale version next to the plugin name. The build now
// fails on version drift; this script is the supported way to clear it.
//
// Usage:  npm run bump-version 0.7.1

const fs = require("fs");

const next = process.argv[2];
if (!next || !/^\d+\.\d+\.\d+(?:-[\w.]+)?$/.test(next)) {
  console.error("Usage: npm run bump-version <semver>   (e.g. 0.7.1, 1.0.0-rc.1)");
  process.exit(1);
}

for (const file of ["package.json", "manifest.json"]) {
  const j = JSON.parse(fs.readFileSync(file, "utf8"));
  const prev = j.version;
  j.version = next;
  // Preserve trailing newline — match the repo's existing JSON style.
  fs.writeFileSync(file, JSON.stringify(j, null, 2) + "\n");
  console.log(`${file}: ${prev} → ${next}`);
}

console.log("\nNext: rebuild + reload the plugin in Obsidian:");
console.log("  npm run build && # then disable+enable Athena in Settings");
