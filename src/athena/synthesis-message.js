"use strict";

/**
 * The chat line for a `kb add` whose wiki synthesis failed.
 *
 * It used to say only "*Synthesis failed* (provider-error). Run kb regen to
 * retry." and drop the provider's own reason. Some reasons can't be fixed by
 * retrying: from Gryphon 2.13.1 (#55) Codex refuses to start when its sandbox
 * can't run (Linux older than 5.13) instead of running unsandboxed, and its
 * message says exactly what to change. The user has to see it.
 */

const MAX = 300;

function _oneLine(s) {
  const t = String(s || "").replace(/\s+/g, " ").trim();
  return t.length > MAX ? t.slice(0, MAX - 1) + "…" : t;
}

function synthesisFailureLine(synthesis, url) {
  if (!synthesis || synthesis.ok) return null;
  if (synthesis.reason === "no-provider") {
    return `*Synthesis skipped:* no LLM provider configured. ${synthesis.detail ||
      "Set up Claude Code or an API key in Settings → Gryphon."}`;
  }
  const why = _oneLine(synthesis.detail) || _oneLine(synthesis.stderr);
  const retry = `Run \`kb regen ${url}\` to retry${why ? " once that's fixed" : ""}.`;
  return why
    ? `*Synthesis failed* (${synthesis.reason}): ${why} ${retry}`
    : `*Synthesis failed* (${synthesis.reason}). ${retry}`;
}

module.exports = { synthesisFailureLine };
