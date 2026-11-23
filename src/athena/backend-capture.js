"use strict";

/**
 * backend-capture.js: read what the Python `kb add` fallback actually did.
 *
 * When the plugin's browser capture can't read a page (a PDF, say), it runs
 * the Python `kb add <url>`, a full ingest: it saves the raw AND creates the
 * wiki page. The plugin used to re-read the raw under a slug it had guessed
 * from the URL; for arxiv.org/pdf/<id> that guess misses the backend's
 * arxiv-<id>.md, so a capture that worked was reported "No content
 * captured." (2026-11-22). These read the backend's own output and files.
 */
const fs = require("fs");
const path = require("path");

/** The vault-relative raw path from the backend's last safe "Saved:" line,
 *  or null. The output includes stderr, which can echo text from the
 *  captured page, so a line is used only when it names a .md file inside
 *  the vault's raw/ tree: the plugin reads that file and hands it on. */
function savedRawPath(output, vaultPath) {
  const all = [...String(output || "").matchAll(/^\s*Saved:\s*(.+?)\s*$/gm)];
  for (let i = all.length - 1; i >= 0; i--) {
    const rel = _insideRaw(all[i][1], vaultPath);
    if (rel) return rel;
  }
  return null;
}

function _insideRaw(p, vaultPath) {
  if (!vaultPath || !p.endsWith(".md")) return null;
  const raw = path.resolve(vaultPath, "raw");
  const abs = path.resolve(vaultPath, p);
  if (!abs.startsWith(raw + path.sep)) return null;
  return path.relative(vaultPath, abs).split(path.sep).join("/");
}

/** The wiki page whose frontmatter lists rawRel (raw_path or raw_paths):
 *  { pageName, summary }, or null. */
function wikiPageForRaw(vaultPath, rawRel) {
  if (!vaultPath || !rawRel) return null;
  const root = path.join(vaultPath, "wiki", "format");
  let dirs = [];
  try { dirs = fs.readdirSync(root); } catch { return null; }
  const esc = rawRel.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const listed = new RegExp(`^(?:raw_path:|\\s+-)\\s*"?${esc}"?\\s*$`, "m");
  for (const d of dirs) {
    let files = [];
    try { files = fs.readdirSync(path.join(root, d)); } catch { continue; }
    for (const f of files) {
      if (!f.endsWith(".md") || f.startsWith("_")) continue;
      let text = "";
      try { text = fs.readFileSync(path.join(root, d, f), "utf8"); } catch { continue; }
      const fm = text.match(/^---\n([\s\S]*?)\n---/);
      if (!fm || !listed.test(fm[1])) continue;
      const sm = fm[1].match(/^summary:\s*(.*)$/m);
      let summary = sm ? sm[1].trim() : null;
      if (summary && summary.startsWith('"')) {
        try { summary = JSON.parse(summary); } catch { summary = summary.replace(/^"|"$/g, ""); }
      }
      return { pageName: f.replace(/\.md$/, ""), summary };
    }
  }
  return null;
}

module.exports = { savedRawPath, wikiPageForRaw };
