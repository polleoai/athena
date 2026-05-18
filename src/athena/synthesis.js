/**
 * Athena wiki synthesis — the JS port of scripts/bulk-llm-regen-summaries.py.
 *
 * Why it exists: pre-1.1 Athena depended on a macOS-only launchd job
 * (com.athena.autoingest) to regenerate the "Pending synthesis" placeholder
 * after kb add. Linux and Windows had no equivalent, so wiki pages on those
 * platforms stayed permanently stubbed. 1.1 retires that dependency: synthesis
 * runs inline as the last step of kb add, on every platform, by routing
 * through Gryphon's provider abstraction (whatever LLM the user has configured
 * for chat is what handles digests).
 *
 * Provider integration is "consumer-side": we use createProvider from
 * @gryphon/provider-runtime but place our digest instructions in the USER
 * prompt rather than the system slot — Gryphon's provider does not expose
 * a customSystemPrompt option, and vendor/gryphon is read-only per the
 * vendoring principle. Models follow detailed user-prompt instructions
 * reliably for structured-JSON output tasks like this.
 */

const path = require("path");

// Vendored Gryphon — same import path the chat-view uses (chat-view.js:48).
const {
  createProvider,
  explainUnavailable,
} = require("../../vendor/gryphon/packages/provider-runtime/src/index");

const {
  qualityCheckSummary,
  qualityCheckBody,
} = require("./synthesis-quality");

const {
  parseFrontmatter,
  getRawBody,
  updateWikiDigest,
} = require("./synthesis-wiki-update");

/**
 * The digest instructions — ported VERBATIM from bulk-llm-regen-summaries.py's
 * DIGEST_SYSTEM_PROMPT. Even small wording drifts change the output enough to
 * matter (the forbidden-opener list is enforced by the model AND by the
 * quality check; if the prompt drifts, the model produces openers that
 * fail validation more often, costing extra retries).
 *
 * Placed in the USER prompt rather than the system slot — see file header
 * for rationale.
 */
const DIGEST_USER_PROMPT_PREAMBLE = `You are writing a structured digest for a page in a personal knowledge base (Obsidian vault). The KB stores technical content: AI papers, security research, blog posts, LinkedIn/X posts, GitHub repos, videos, and conference talks. The user is a practitioner who needs to quickly recall what each page contains and why it matters.

You will receive the page title, source type, and body text. Produce a JSON object with exactly two keys:

"summary": One prose paragraph for the frontmatter summary field. Rules:
  - Front-load the WHAT in the first 5-15 words. State directly what the page is.
  - NEVER start with "This article describes", "This page is about", "An overview of", "A guide to", "This document explains", "The article discusses", or similar filler.
  - Name 2-4 concrete distinguishing details: specific numbers, named techniques, named entities, key claims.
  - If the body is sparse (only a title + URL, or only HTML chrome with no meaningful content), say so honestly: "Sparse capture — only the title and URL are stored; see source for content."
  - NEVER infer or invent author names, organization affiliations, or proper nouns not in the source. If the body doesn't name the author, write "the author" or "the post".
  - Plain prose. No headers, bullets, or line breaks. No markdown other than backticks for code identifiers.
  - Length: target 250-400 characters; HARD CEILING 550 characters.
  - Forbidden marketing words: powerful, cutting-edge, innovative, robust, seamless, comprehensive, leverages, unleashes, revolutionary, game-changing, state-of-the-art, world-class, next-generation.

"body": Structured markdown digest with the following sections (use exactly these headers):

## Key Findings

3 to 7 bullet points. Each bullet is one concrete, specific takeaway the user can recall months later. Include numbers, named methods, named entities, specific claims. Avoid vague generalities like "the author discusses X" or "covers important topics". If the source is sparse or thin, say so in 1-2 bullets.

## Methods / Architecture

INCLUDE ONLY for: technical papers, repos, deep-dive blog posts about systems or algorithms, security research with specific techniques. OMIT for: LinkedIn opinion posts, news articles, videos without technical detail, social media posts, conference agendas, sparse captures.

If included: 2-5 sentences describing the system design, algorithm, or methodology. Be specific — name the components, data structures, or steps.

## Notable Quotes

INCLUDE ONLY if the source contains memorable, quotable text (a sharp insight, a striking claim, a useful definition). OMIT if no strong quotes exist — do NOT invent quotes.

If included: 1-3 blockquotes using > syntax. Copy verbatim from the body text.

## Relevance

1-3 sentences. Why this page matters in the context of AI engineering, security research, knowledge management, or whatever domain it belongs to. What would the user do with this information?

---

SECTION RULES:
- Include all four sections by default.
- Omit "Methods / Architecture" for non-technical content.
- Omit "Notable Quotes" if no strong quotes exist.
- Never fabricate content. If a section would be empty or invented, omit it.
- Use standard markdown. No HTML.

OUTPUT FORMAT: Return ONLY a JSON object on a single line. No preamble, no explanation, no markdown code fences. Example shape:
{"summary": "...", "body": "## Key Findings\\n\\n- ...\\n\\n## Relevance\\n\\n..."}`;

/**
 * Build the user prompt: digest instructions + page metadata + body.
 *
 * Format matches the Python script exactly so the model produces
 * comparable output. retryReason (optional) is appended on retry attempts
 * with a one-line note pointing the model at what failed last time —
 * a small but measurable improvement in retry success rate (~30% per the
 * Python script's logs).
 */
function composeUserPrompt({
  title,
  sourceType,
  existingSummary,
  body,
  retryReason,
}) {
  const parts = [
    DIGEST_USER_PROMPT_PREAMBLE,
    "",
    "---",
    "",
    `PAGE TITLE: ${title || "(no title)"}`,
    `SOURCE TYPE: ${sourceType || "unknown"}`,
    "EXISTING SUMMARY (may be low-quality, use for context only):",
    existingSummary || "(none)",
    "",
    "PAGE BODY (source content):",
    body || "(empty)",
    "",
    "Generate the JSON digest now.",
  ];
  if (retryReason) {
    parts.push("");
    parts.push(`(Previous attempt failed quality check: ${retryReason}. Fix this in your next attempt.)`);
  }
  return parts.join("\n");
}

/**
 * Parse the model's JSON response. Tolerates the two common deviations
 * we've seen in the Python script's logs:
 *   1. Model wraps output in markdown code fences (```json ... ```)
 *   2. Model adds a preamble/postscript around the JSON object
 *
 * Returns { ok: true, summary, body } on success, { ok: false, reason } on
 * any parse failure. The caller (synthesizeWikiPage) treats parse failures
 * the same as quality failures — retry up to maxRetries times.
 */
function parseDigestResponse(text) {
  if (!text || typeof text !== "string") {
    return { ok: false, reason: "empty-response" };
  }
  let out = text.trim();
  // Strip markdown code fences if the model wrapped its output.
  out = out.replace(/^```(?:json)?\s*/gm, "").replace(/\s*```\s*$/gm, "").trim();

  let parsed;
  try {
    parsed = JSON.parse(out);
  } catch (_) {
    // Try to find a JSON object embedded in the text (matches the Python
    // script's r'\{.*\}' fallback). [\s\S] = any char including newline.
    const m = out.match(/\{[\s\S]*\}/);
    if (!m) return { ok: false, reason: "no-json-found" };
    try {
      parsed = JSON.parse(m[0]);
    } catch (_e2) {
      return { ok: false, reason: "json-parse-fail" };
    }
  }

  if (!parsed || typeof parsed !== "object") {
    return { ok: false, reason: "not-object" };
  }

  let summary = (parsed.summary || "").trim();
  let body = (parsed.body || "").trim();

  // Collapse accidental newlines in summary — models occasionally split a
  // long sentence across two lines even though the rules say one paragraph.
  summary = summary.replace(/\s*\n\s*/g, " ").trim();
  // Strip surrounding quotes if the model double-wrapped.
  if (
    (summary.startsWith('"') && summary.endsWith('"')) ||
    (summary.startsWith("'") && summary.endsWith("'"))
  ) {
    summary = summary.slice(1, -1).trim();
  }
  // Strip "Summary:" preface if it slipped through.
  summary = summary.replace(/^(Summary|SUMMARY)\s*[:\-]\s*/, "");

  return { ok: true, summary, body };
}

/**
 * Call Gryphon's provider with the digest prompt. Returns
 * { ok: true, text } or { ok: false, reason }.
 *
 * Uses createProvider directly — same call shape chat-view uses, but
 * passes resumeSessionId: null so the synthesis call doesn't attach to
 * the user's running chat session. Model defaults to the user's
 * Gryphon-configured choice (settings.model); no override.
 */
async function callProvider(plugin, prompt) {
  const vaultPath = plugin.app.vault.adapter.basePath;
  const provider = createProvider(plugin, vaultPath, {
    model: (plugin.settings && plugin.settings.model) || undefined,
    resumeSessionId: null,
  });
  if (!provider) {
    return {
      ok: false,
      reason: "no-provider",
      detail: explainUnavailable(plugin),
    };
  }

  let stderrBuf = "";
  provider.onMessage = () => { /* discard streaming deltas — only final text matters */ };
  provider.onError = (text) => { stderrBuf += text + "\n"; };

  try {
    const result = await provider.send(prompt);
    return { ok: true, text: (result && result.text) || "", cost: (result && result.cost) || 0 };
  } catch (e) {
    return {
      ok: false,
      reason: "provider-error",
      detail: (e && e.message) || String(e),
      stderr: stderrBuf.trim().slice(0, 500),
    };
  }
}

/**
 * Synthesize a wiki page in place.
 *
 * The main public entry. Reads the wiki, loads the raw body, calls the
 * LLM, validates, writes the result back. Retries up to maxRetries times
 * on parse/quality failure with the failure reason fed back into the
 * prompt.
 *
 * Returns:
 *   { ok: true, summary, body, changed, cost, attempts }
 *   { ok: false, reason, detail, attempts }
 *
 * Reasons:
 *   "no-provider"       — Gryphon has no LLM configured (CLI/key missing)
 *   "io-error"          — couldn't read the wiki or raw file
 *   "no-frontmatter"    — wiki page has no YAML frontmatter
 *   "provider-error"    — provider.send threw or returned no text
 *   "parse-fail"        — final attempt couldn't be parsed as JSON
 *   "quality-fail"      — final attempt failed quality checks
 */
async function synthesizeWikiPage(plugin, wikiAbsPath, options = {}) {
  const maxRetries = typeof options.maxRetries === "number" ? options.maxRetries : 2;
  const onStatus = typeof options.onStatus === "function" ? options.onStatus : () => {};
  const vaultPath = plugin.app.vault.adapter.basePath;

  let wikiText;
  try {
    wikiText = require("fs").readFileSync(wikiAbsPath, "utf-8");
  } catch (e) {
    return { ok: false, reason: "io-error", detail: (e && e.message) || String(e), attempts: 0 };
  }
  const { fm } = parseFrontmatter(wikiText);
  if (!fm || Object.keys(fm).length === 0) {
    return { ok: false, reason: "no-frontmatter", attempts: 0 };
  }

  const title = fm.title || path.basename(wikiAbsPath, ".md");
  const sourceType = fm.source_type || "unknown";
  const existingSummary = fm.summary || "";
  const body = getRawBody(wikiAbsPath, fm, vaultPath);

  let attempts = 0;
  let retryReason = null;
  let totalCost = 0;
  let lastDetail = "";

  while (attempts <= maxRetries) {
    attempts += 1;
    onStatus(attempts === 1 ? "Synthesizing..." : `Synthesizing... (retry ${attempts - 1})`);

    const prompt = composeUserPrompt({ title, sourceType, existingSummary, body, retryReason });
    const llmResult = await callProvider(plugin, prompt);
    if (!llmResult.ok) {
      // Provider-level failures (no key, network error, abort) aren't
      // worth retrying — fail fast so the caller can surface the actual
      // setup hint to the user.
      return {
        ok: false,
        reason: llmResult.reason,
        detail: llmResult.detail || llmResult.stderr || "",
        attempts,
      };
    }
    totalCost += llmResult.cost || 0;

    const parsed = parseDigestResponse(llmResult.text);
    if (!parsed.ok) {
      retryReason = `output-not-valid-json (${parsed.reason})`;
      lastDetail = retryReason;
      continue;
    }

    const summaryCheck = qualityCheckSummary(parsed.summary);
    if (!summaryCheck.ok) {
      retryReason = `summary-quality-fail (${summaryCheck.reason})`;
      lastDetail = retryReason;
      continue;
    }

    const bodyCheck = qualityCheckBody(parsed.body);
    if (!bodyCheck.ok) {
      retryReason = `body-quality-fail (${bodyCheck.reason})`;
      lastDetail = retryReason;
      continue;
    }

    // All checks passed — write the result back.
    onStatus("Writing digest...");
    const writeResult = updateWikiDigest(wikiAbsPath, parsed.summary, parsed.body);
    return {
      ok: true,
      summary: parsed.summary,
      body: parsed.body,
      changed: writeResult.changed,
      cost: totalCost,
      attempts,
    };
  }

  // Exhausted retries.
  return {
    ok: false,
    reason: lastDetail.includes("quality") ? "quality-fail" : "parse-fail",
    detail: lastDetail,
    attempts,
    cost: totalCost,
  };
}

/**
 * Find all wiki pages still showing the "Pending synthesis" placeholder.
 * Used by `kb regen --all-pending`.
 *
 * Returns array of absolute paths. Walks wiki/format/* (the layout the
 * Python script also targets — wiki/topics, wiki/entities, etc. are
 * hand-curated and don't get auto-regenerated).
 */
function findPendingPages(plugin) {
  const fs = require("fs");
  const vaultPath = plugin.app.vault.adapter.basePath;
  const wikiFormatRoot = path.join(vaultPath, "wiki", "format");
  if (!fs.existsSync(wikiFormatRoot)) return [];

  const PLACEHOLDER = "*Pending synthesis";
  const out = [];
  function walk(dir) {
    let entries;
    try {
      entries = fs.readdirSync(dir, { withFileTypes: true });
    } catch (_) {
      return;
    }
    for (const ent of entries) {
      const full = path.join(dir, ent.name);
      if (ent.isDirectory()) {
        walk(full);
      } else if (ent.isFile() && ent.name.endsWith(".md")) {
        try {
          const text = fs.readFileSync(full, "utf-8");
          if (text.includes(PLACEHOLDER)) out.push(full);
        } catch (_) {
          // Unreadable file — skip.
        }
      }
    }
  }
  walk(wikiFormatRoot);
  return out;
}

module.exports = {
  DIGEST_USER_PROMPT_PREAMBLE,
  composeUserPrompt,
  parseDigestResponse,
  callProvider,
  synthesizeWikiPage,
  findPendingPages,
};
