/**
 * claude-binary.js — which Claude Code binary Athena spawns (#217).
 *
 * Athena used to spawn `this.settings.claudePath || findClaudeBinary()`.
 * settings come from the vault's .obsidian/plugins/athena/data.json, which
 * travels with the vault: a shared, synced or cloned vault could set
 * claudePath to a script it ships, and Athena ran it on the first KB
 * operation (sibling of gryphon-dev#30 G1).
 *
 * Now the binary comes from Gryphon's resolveCliPath, the same resolver
 * Gryphon's own spawns use: a path confirmed on THIS machine (Gryphon's
 * security store, scoped to host "athena") or auto-detection. Paths inside
 * the vault are rejected by the resolver. Vault settings are never read.
 *
 * protect is required by its package entry (dist/index.js), the same module
 * Gryphon's compiled chat view loads through @polleoai/gryphon-protect, so
 * the bundle holds one store and one rejection log, not two.
 */
"use strict";

const { resolveCliPath } = require("../../vendor/gryphon/packages/protect/dist/index.js");
const { findClaudeBinary } = require("../../vendor/gryphon/src/utils");

const ATHENA_SECURITY_HOST_ID = "athena";

/**
 * Path of the Claude Code binary to spawn, or null if none is installed.
 * @param {object} plugin  the Athena plugin (for its Obsidian `app`); its
 *   settings are deliberately not consulted.
 * @param {object} [deps]  test seams: { resolve, detect }.
 */
function resolveClaudeBinary(plugin, deps = {}) {
  const resolve = deps.resolve || resolveCliPath;
  try {
    const r = resolve("claude-code", {
      app: plugin && plugin.app,
      hostPlugin: plugin,
      hostId: ATHENA_SECURITY_HOST_ID,
      ...(deps.detect ? { detect: deps.detect } : {}),
    });
    return (r && r.path) || null;
  } catch (e) {
    // The resolver failing must not stop KB work: fall back to detection on
    // this machine, which never reads vault data.
    console.warn(`[athena] resolving the Claude Code path failed (${e && e.message}); using auto-detection`);
    return (deps.detect ? deps.detect("claude-code") : findClaudeBinary()) || null;
  }
}

module.exports = { resolveClaudeBinary, ATHENA_SECURITY_HOST_ID };
