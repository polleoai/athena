"use strict";

/**
 * Which command-line tool Settings → Athena → "Test Connection" checks for a
 * provider preference, found the way Gryphon finds it when it starts a chat.
 *
 * The button used a hand-written map of bare names ("codex", "gemini"). Two
 * failures followed: Antigravity, which Gryphon offers (and 2.13.0 fixed for
 * agy 1.5.9), had no entry, so the button said "not found" for a working
 * install; and a bare name only resolves on Obsidian's PATH, which on a
 * Finder/Dock launch on macOS lacks ~/.local/bin, Homebrew and npm's global
 * dir, so an installed Codex or Gemini CLI read as missing too. Gryphon's
 * finders search those places; Claude keeps Athena's own resolver, which
 * honours the security host's pinned binary.
 */

const CLI_KINDS = {
  "claude-code":     { label: "Claude Code",     finder: "claude" },
  "auto":            { label: "Claude Code (auto)", finder: "claude" },
  "codex-cli":       { label: "Codex CLI",       finder: "findCodexBinary" },
  "gemini-cli":      { label: "Gemini CLI",      finder: "findGeminiBinary" },
  "antigravity-cli": { label: "Antigravity CLI", finder: "findAntigravityBinary" },
};

const API_KINDS = new Set(["anthropic-api", "openai-api", "google-api"]);

function _defaultDeps() {
  const runtime = require("../../vendor/gryphon/packages/provider-runtime/src/index");
  const { resolveClaudeBinary } = require("./claude-binary");
  return {
    resolveClaudeBinary,
    findCodexBinary: runtime.findCodexBinary,
    findGeminiBinary: runtime.findGeminiBinary,
    findAntigravityBinary: runtime.findAntigravityBinary,
  };
}

/**
 * @returns {{kind:"api"}} for an HTTP-API provider,
 *          {{kind:"cli", label, bin}} for a CLI (bin null when not found),
 *          {{kind:"unknown", label}} for a preference Athena doesn't know.
 */
function cliForProvider(provider, plugin, deps = _defaultDeps()) {
  const pref = provider || "auto";
  if (API_KINDS.has(pref)) return { kind: "api" };
  const entry = CLI_KINDS[pref];
  if (!entry) return { kind: "unknown", label: pref };
  let bin = null;
  try {
    bin = entry.finder === "claude" ? deps.resolveClaudeBinary(plugin) : deps[entry.finder]();
  } catch (e) {
    console.warn(`[athena] looking for the ${entry.label} binary failed:`, e);
  }
  return { kind: "cli", label: entry.label, bin: bin || null };
}

module.exports = { cliForProvider, CLI_KINDS, API_KINDS };
