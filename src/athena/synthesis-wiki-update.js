/**
 * Wiki page parsing and digest write-back for Athena synthesis.
 *
 * Ported from scripts/bulk-llm-regen-summaries.py (parse_frontmatter,
 * get_raw_body, _replace_summary_in_text, update_wiki_digest).
 *
 * Module owns the filesystem-touching half of synthesis: read the wiki +
 * raw, compute the rewrite, write back atomically. synthesis.js orchestrates;
 * synthesis-quality.js validates; this file is the I/O layer.
 */

const fs = require("fs");
const path = require("path");

// Token budget for the body sent to the LLM. ~12K chars maps to roughly
// 3K input tokens — generous for most posts/repos, short enough to keep
// a single regen under a few cents on paid SDK providers. Long PDFs and
// papers get truncated; this matches the Python script's behavior so the
// JS port produces comparable digests.
const MAX_BODY_CHARS = 12000;

/**
 * Parse YAML-ish frontmatter from a wiki page.
 *
 * Returns { fm: object, fmEnd: number } where fmEnd is the byte offset
 * of the first byte after the closing `---\n`. Returns { fm: {}, fmEnd: 0 }
 * if the file has no frontmatter.
 *
 * Mirrors the Python parse_frontmatter — handles scalar `key: value`,
 * empty `key:` (start of a list), and indented list items `  - "value"`.
 * Does NOT handle nested maps; Athena frontmatter doesn't use them.
 */
function parseFrontmatter(text) {
  if (!text.startsWith("---")) {
    return { fm: {}, fmEnd: 0 };
  }
  // Find the closing `---` line. Search from index 3 to skip the opening one.
  const rest = text.slice(3);
  const closeMatch = rest.match(/\n---[\s]*\n/);
  if (!closeMatch) {
    return { fm: {}, fmEnd: 0 };
  }
  const fmBlock = rest.slice(0, closeMatch.index);
  const fm = {};
  let curKey = null;
  for (const line of fmBlock.split("\n")) {
    if (!line.trim()) continue;
    if (line.startsWith("  - ") && curKey !== null) {
      if (!Array.isArray(fm[curKey])) fm[curKey] = [];
      fm[curKey].push(line.slice(4).trim().replace(/^["']|["']$/g, ""));
      continue;
    }
    const m = line.match(/^([\w_-]+)\s*:\s*(.*)$/);
    if (!m) continue;
    curKey = m[1].trim();
    const val = m[2].trim();
    if (val) {
      fm[curKey] = val.replace(/^["']|["']$/g, "");
    } else {
      fm[curKey] = [];
    }
  }
  // closeMatch.index is relative to `rest` (which started at offset 3),
  // and the match itself is `\n---\s*\n` — fmEnd needs to be the byte
  // AFTER the closing line. closeMatch.index + closeMatch[0].length + 3.
  const fmEnd = closeMatch.index + closeMatch[0].length + 3;
  return { fm, fmEnd };
}

/**
 * Return the body content for synthesis. Prefers the raw source pointed
 * to by `raw_path:` in frontmatter — that's the ground truth. Falls back
 * to the wiki body (sans its own frontmatter) if the raw is missing.
 *
 * Truncates to MAX_BODY_CHARS with an honest "[... truncated ...]" marker
 * so the model knows it's not seeing the whole thing.
 */
function getRawBody(wikiAbsPath, fm, vaultPath) {
  const rawPathRel = fm && fm.raw_path;
  if (rawPathRel && typeof rawPathRel === "string") {
    const rawAbs = path.join(vaultPath, rawPathRel);
    try {
      const stat = fs.statSync(rawAbs);
      if (stat.isFile()) {
        let rawText = fs.readFileSync(rawAbs, "utf-8");
        // Strip raw's own frontmatter if present.
        if (rawText.startsWith("---")) {
          const close = rawText.slice(3).match(/\n---[\s]*\n/);
          if (close) {
            rawText = rawText.slice(close.index + close[0].length + 3).trim();
          }
        }
        if (rawText.length > MAX_BODY_CHARS) {
          rawText = rawText.slice(0, MAX_BODY_CHARS) +
            "\n\n[... truncated for token budget ...]";
        }
        return rawText;
      }
    } catch (_) {
      // Fall through to wiki-body fallback.
    }
  }
  // Fallback: read wiki body sans frontmatter.
  const text = fs.readFileSync(wikiAbsPath, "utf-8");
  const { fmEnd } = parseFrontmatter(text);
  let body = text.slice(fmEnd).trim();
  if (body.length > MAX_BODY_CHARS) {
    body = body.slice(0, MAX_BODY_CHARS) +
      "\n\n[... truncated for token budget ...]";
  }
  return body;
}

/**
 * Replace (or insert) the `summary:` field in frontmatter with a new value.
 *
 * Tries the double-quoted form first, then the unquoted form, then falls
 * back to inserting a new line just before the closing `---`. The escape
 * rules match the YAML emitter in bin/lib/wiki_page.py — backslash + quote.
 */
function replaceSummaryInText(text, newSummary) {
  const safe = newSummary.replace(/\\/g, "\\\\").replace(/"/g, '\\"');
  // Try double-quoted match first.
  let count = 0;
  let result = text.replace(
    /^summary:\s*"[^"\n]*"\s*$/m,
    () => { count += 1; return `summary: "${safe}"`; }
  );
  if (count === 0) {
    // Try unquoted form.
    result = text.replace(
      /^summary:\s*[^\n]*$/m,
      () => { count += 1; return `summary: "${safe}"`; }
    );
  }
  if (count === 0) {
    // No existing summary line — insert just before the closing `---`.
    if (text.startsWith("---")) {
      const close = text.slice(3).match(/\n---[\s]*\n/);
      if (close) {
        const insertAt = close.index + 3;
        const prefix = insertAt > 0 && text[insertAt - 1] !== "\n" ? "\n" : "";
        result = text.slice(0, insertAt)
          + prefix + `summary: "${safe}"\n`
          + text.slice(insertAt);
      }
    }
  }
  return result;
}

/**
 * Rewrite the body zone of a wiki page with a new structured digest.
 *
 * The body zone spans from the line AFTER the canonical "Local Copy"
 * link line through the line BEFORE the "## Connections" or "## Keywords"
 * section (whichever comes first). Pre-zone (frontmatter + Source/Local
 * Copy line) and post-zone (Connections/Keywords) are preserved verbatim.
 *
 * Returns { changed: boolean } — true if the file was (or would be on
 * dryRun=false) modified.
 */
function updateWikiDigest(wikiAbsPath, newSummary, newBody, options = {}) {
  const dryRun = options.dryRun === true;
  const text = fs.readFileSync(wikiAbsPath, "utf-8");

  let newText = replaceSummaryInText(text, newSummary);

  if (!newText.startsWith("---")) {
    // No frontmatter — refuse to touch the file rather than corrupting it.
    return { changed: false, reason: "no-frontmatter" };
  }
  const close = newText.slice(3).match(/\n---[\s]*\n/);
  if (!close) {
    return { changed: false, reason: "no-frontmatter-close" };
  }
  const fmEnd = close.index + close[0].length + 3;
  const bodyZone = newText.slice(fmEnd);
  const lines = bodyZone.split("\n");

  // Find the canonical "Source · [[Local Copy]]" line — emitted by
  // bin/lib/wiki_page.py for every wiki page Athena creates. Anything
  // above this line (frontmatter, source/local-copy attribution) is
  // preserved verbatim.
  let sourceEndIdx = lines.findIndex((line) => line.includes("Local Copy"));
  if (sourceEndIdx < 0) {
    // Fallback: first non-empty line after frontmatter.
    sourceEndIdx = lines.findIndex((line) => line.trim());
  }
  if (sourceEndIdx < 0) {
    return { changed: false, reason: "no-source-line" };
  }

  // Find where Connections / Keywords begin — those sections are
  // preserved verbatim because Connections is partially user-owned
  // (the body version coexists with frontmatter `related:`).
  const sectionsStartIdx = lines.findIndex(
    (line) => line.startsWith("## Connections") || line.startsWith("## Keywords"),
  );

  const prefixLines = lines.slice(0, sourceEndIdx + 1);
  const suffixLines = sectionsStartIdx >= 0 ? lines.slice(sectionsStartIdx) : [];

  const newBodyZone = (
    prefixLines.join("\n")
    + "\n\n"
    + newBody.trim()
    + "\n\n"
    + (suffixLines.length ? suffixLines.join("\n") : "")
  ).replace(/\s+$/, "") + "\n";

  const result = newText.slice(0, fmEnd) + newBodyZone;

  if (result === text) {
    return { changed: false, reason: "no-change" };
  }
  if (!dryRun) {
    fs.writeFileSync(wikiAbsPath, result, "utf-8");
  }
  return { changed: true };
}

module.exports = {
  MAX_BODY_CHARS,
  parseFrontmatter,
  getRawBody,
  replaceSummaryInText,
  updateWikiDigest,
};
