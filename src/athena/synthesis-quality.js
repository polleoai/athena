/**
 * Quality checks for Athena wiki digests.
 *
 * Ported from scripts/bulk-llm-regen-summaries.py (quality_check_summary +
 * quality_check_body). When a check fails the caller (synthesis.js) retries
 * the LLM call with the failure reason fed back into the user prompt — same
 * retry behavior the Python script has had since the bulk regen of 252 pages
 * on 2026-05-08.
 *
 * Pure functions — no I/O, no async. Easy to unit-test in isolation.
 */

// Length bounds for the frontmatter `summary:` field. Front-loaded WHAT in
// 5-15 words usually lands at 250-400 chars; the hard ceiling is 600 to
// allow sparse-capture admissions ("Sparse capture — only the title and URL
// are stored") which include a short reason and don't fit in the 400 budget.
const MIN_SUMMARY = 100;
const MAX_SUMMARY = 600;

// Forbidden openers — phrases that make the summary read like template
// boilerplate instead of a front-loaded WHAT statement. The model is
// instructed to avoid them in the system prompt but occasionally drifts;
// the quality check is the safety net.
const FORBIDDEN_OPENERS = Object.freeze([
  "this article",
  "this page",
  "this document",
  "this repository",
  "this repo",
  "this paper",
  "this video",
  "an overview of",
  "a guide to",
  "the article",
  "the page",
  "the document",
]);

// Forbidden marketing words — the LinkedIn-slop vocabulary that makes
// summaries sound like vendor copy instead of practitioner notes.
const FORBIDDEN_WORDS = Object.freeze([
  "cutting-edge",
  "state-of-the-art",
  "world-class",
  "next-generation",
  "game-changing",
  "revolutionary",
]);

/**
 * Validate a frontmatter `summary:` string.
 *
 * Returns { ok: true } on pass, { ok: false, reason: string } on fail.
 * The reason is short enough to feed back into the retry prompt.
 */
function qualityCheckSummary(summary) {
  if (!summary || !summary.trim()) {
    return { ok: false, reason: "empty" };
  }
  const s = summary.trim();
  const low = s.toLowerCase();

  // Length check — "sparse capture" admissions are allowed to be short.
  if (s.length < MIN_SUMMARY && !low.includes("sparse capture")) {
    return {
      ok: false,
      reason: `too-short (${s.length} chars, no sparse-capture admission)`,
    };
  }
  if (s.length > MAX_SUMMARY) {
    return { ok: false, reason: `too-long (${s.length} chars)` };
  }

  for (const opener of FORBIDDEN_OPENERS) {
    if (low.startsWith(opener)) {
      return { ok: false, reason: `forbidden-opener (${JSON.stringify(opener)})` };
    }
  }
  for (const word of FORBIDDEN_WORDS) {
    if (low.includes(word)) {
      return { ok: false, reason: `forbidden-word (${JSON.stringify(word)})` };
    }
  }

  if (s.includes("\n")) {
    return { ok: false, reason: "contains-newline" };
  }

  // Unicode-math-bold leak: some models occasionally emit bolded glyphs
  // (𝐓𝐡𝐢𝐬 𝐢𝐬 𝐛𝐨𝐥𝐝) from the Mathematical Alphanumeric Symbols block
  // when the prompt mentions "bold" or "emphasize". Those glyphs look
  // like normal letters in rendered Obsidian but break search and copy.
  for (const ch of s) {
    const cp = ch.codePointAt(0);
    if (cp >= 0x1D400 && cp <= 0x1D7FF) {
      return {
        ok: false,
        reason: `unicode-math-bold-leak (char ${JSON.stringify(ch)})`,
      };
    }
  }

  return { ok: true };
}

/**
 * Validate the structured digest body.
 *
 * Just checks that the Key Findings section exists and has content —
 * everything else is content-quality which the model handles via the
 * system prompt. Mirrors the Python version exactly.
 */
function qualityCheckBody(body) {
  if (!body || !body.trim()) {
    return { ok: false, reason: "body-empty" };
  }
  if (!body.includes("## Key Findings")) {
    return { ok: false, reason: "missing-key-findings" };
  }
  // Key Findings must have at least one non-placeholder line of content.
  const kfMatch = body.match(/## Key Findings\n+([\s\S]*?)(?=\n## |$)/);
  if (kfMatch) {
    const kfContent = kfMatch[1].trim();
    if (!kfContent || kfContent.startsWith("*Pending")) {
      return { ok: false, reason: "key-findings-empty" };
    }
  }
  return { ok: true };
}

module.exports = {
  MIN_SUMMARY,
  MAX_SUMMARY,
  FORBIDDEN_OPENERS,
  FORBIDDEN_WORDS,
  qualityCheckSummary,
  qualityCheckBody,
};
