/**
 * model-settings.js — Athena's model ids, routed through Gryphon's registry.
 *
 * Athena keeps its own settings (its own data.json), so Gryphon's load-time
 * model migration never touches them: a retired or legacy id would stay in
 * Athena's settings forever. And llmProcessContent spawns the claude CLI
 * directly, so the model it passes must be a Claude id. Gryphon's provider
 * isn't in that path to resolve aliases or reject other vendors (gryphon
 * PR #44, relayed by PGM on 2026-11-16).
 *
 * settings.model comes from vault data.json, which a shared or cloned vault
 * can set (cf. #217), so the CLI value is checked against a strict Claude id
 * shape before it becomes an argv entry.
 */
"use strict";

const registry = require("../../vendor/gryphon/packages/provider-config/dist/registry.js");
const anthropic = require("../../vendor/gryphon/packages/provider-config/dist/pricing/anthropic.js");

// A concrete Claude id: lowercase, starts "claude-", optional "[1m]" suffix.
// Anything else (flags, whitespace, paths, other vendors) is refused.
const CLAUDE_ID = /^claude-[a-z0-9][a-z0-9.-]*(\[1m\])?$/;

const MODEL_KEYS = ["model", "fallbackModel"];

/**
 * The --model value for a direct claude CLI spawn, or null when the
 * configured model can't be sent to Claude Code (the caller then skips the
 * LLM step, as it does when no binary is found).
 */
function cliModelArg(settings) {
  const raw = settings && settings.model;
  if (raw !== undefined && raw !== null && typeof raw !== "string") {
    console.warn("[athena] model setting is not a string; skipping the Claude Code call");
    return null;
  }
  let id = anthropic.resolveModel(raw || "");
  id = registry.retiredModelSuccessor(id) || id;
  if (!CLAUDE_ID.test(id)) {
    console.warn(`[athena] model "${String(raw).slice(0, 60)}" is not a Claude model; ` +
      "skipping the Claude Code call (pick a Claude model in Settings → Athena)");
    return null;
  }
  return id;
}

// One copy of a vault-supplied value fit for a Notice: a single line, bounded.
function _shown(v) {
  const s = String(v).replace(/[\r\n\t]+/g, " ").trim();
  return s.length > 60 ? s.slice(0, 57) + "..." : s;
}

/**
 * cliModelArg for a plugin, never silent: when the model is refused, tell
 * the user once per session (notify, e.g. an Obsidian Notice) and log it on
 * every skip. A step that quietly doesn't run reads as success (PGM,
 * 2026-11-16).
 */
function modelForCli(plugin, { notify } = {}) {
  const id = cliModelArg(plugin && plugin.settings);
  if (id) return id;
  const value = _shown(plugin && plugin.settings ? plugin.settings.model : "");
  console.warn(`[athena] LLM processing skipped: model setting "${value}" isn't a Claude model`);
  if (plugin && !plugin._athenaModelSkipNoticed) {
    plugin._athenaModelSkipNoticed = true;
    if (typeof notify === "function") {
      notify(`Athena: the model setting "${value}" isn't a Claude model, so LLM processing ` +
        "was skipped. Pick a Claude model in Settings → Athena.");
    }
  }
  return null;
}

/**
 * Rewrite legacy aliases and retired ids in Athena's settings, the way
 * Gryphon migrates its own: the alias step on `model`, then the retirement
 * step on every model field. Mutates `settings`; returns [{ key, from, to }].
 * Idempotent: successors are current ids.
 */
function migrateModelSettings(settings) {
  const changes = [];
  if (!settings || typeof settings !== "object") return changes;
  const aliases = registry.legacyAliasMigrationFor("anthropic");
  const start = {};
  for (const key of MODEL_KEYS) start[key] = settings[key];
  const m = settings.model;
  if (typeof m === "string" && Object.prototype.hasOwnProperty.call(aliases, m)) {
    settings.model = aliases[m];
  }
  for (const key of MODEL_KEYS) {
    const v = settings[key];
    const next = typeof v === "string" ? registry.retiredModelSuccessor(v) : null;
    if (next) settings[key] = next;
  }
  for (const key of MODEL_KEYS) {
    if (settings[key] !== start[key]) changes.push({ key, from: start[key], to: settings[key] });
  }
  return changes;
}

module.exports = { cliModelArg, modelForCli, migrateModelSettings, CLAUDE_ID };
