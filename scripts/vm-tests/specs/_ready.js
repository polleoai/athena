"use strict";

/**
 * _ready.js: Athena's "finished onload" predicate, for argus obsidianSpec's
 * readyCheck.
 *
 * argus's default readiness ("every plugin id is present on window.app") is
 * weaker than the specs need: Obsidian lists a plugin before its async onload
 * finishes. The fixture installs and enables both plugins, but Athena includes
 * all of Gryphon (it bundles Gryphon's chat view from vendor/gryphon/src), so
 * its onload disables the standalone Gryphon and saves that (mutual
 * exclusivity, src/athena/plugin.js:913). Requiring both loaded, as before,
 * could never pass: matrix -4 (11-21) timed out on every vault at 120 s with
 * gryphon disabled and athena loaded. So the vault is ready when:
 *   - athena is loaded, with its settings and runMechanical,
 *   - the standalone gryphon is neither enabled nor loaded (the disable ran),
 *   - athena's view type and the open-chat command are registered.
 *
 * Self-contained, with no closures and no requires: _helpers.js runs it inside
 * the page as `(${athenaReady})(window.app)`, and tests run it in node
 * against a fake app.
 */
function athenaReady(app) {
  const ps = app && app.plugins;
  const p = ps && ps.plugins;
  if (!p || !p.athena) return false;
  if (!p.athena.settings || typeof p.athena.runMechanical !== "function") return false;
  if (!ps.enabledPlugins || typeof ps.enabledPlugins.has !== "function") return false;
  if (p.gryphon || ps.enabledPlugins.has("gryphon")) return false;
  const views = app.viewRegistry && app.viewRegistry.viewByType;
  if (!views || !views["athena-view"]) return false;
  const cmds = app.commands && app.commands.commands;
  return !!(cmds && cmds["athena:open-chat"]);
}

/**
 * Load state of each plugin, for the diagnostic below (matrix -3, 11-21:
 * gryphon "present=no" on Debian with no guest console to say why).
 *   known   Obsidian read its manifest (files reached the vault)
 *   enabled it is in community-plugins.json (the fixture enabled it)
 *   loaded  its onload ran far enough to be registered
 * known && enabled && !loaded, for good, means onload threw. Self-contained.
 */
function pluginLoadState(app) {
  const ps = app && app.plugins;
  const one = (id) => ({
    known: !!(ps && ps.manifests && ps.manifests[id]),
    enabled: !!(ps && ps.enabledPlugins && typeof ps.enabledPlugins.has === "function" && ps.enabledPlugins.has(id)),
    loaded: !!(ps && ps.plugins && ps.plugins[id]),
  });
  return { gryphon: one("gryphon"), athena: one("athena") };
}

/**
 * The readyCheck _helpers.js hands to obsidianSpec: athenaReady, plus
 * diagnostics in the run output (vm-run.log). On a page's first check it
 * attaches console-error and pageerror listeners (errors before the CDP
 * connect are missed, so the flags are the surer signal), and it logs the
 * load state each time it changes, so a slow load and a throw look different.
 */
function makeReadyCheck({ log = (m) => console.log(m) } = {}) {
  const watched = new WeakSet();
  const last = new WeakMap();
  return async (page) => {
    if (!watched.has(page)) {
      watched.add(page);
      page.on("console", (m) => { if (m.type() === "error") log(`[athena-ready] console error: ${m.text()}`); });
      page.on("pageerror", (e) => log(`[athena-ready] pageerror: ${(e && e.stack) || e}`));
    }
    const r = await page.evaluate(
      `(() => ({ ready: (${athenaReady})(window.app), state: (${pluginLoadState})(window.app) }))()`);
    const s = JSON.stringify(r.state);
    if (last.get(page) !== s) { last.set(page, s); log(`[athena-ready] plugin state ready=${r.ready} ${s}`); }
    return r.ready;
  };
}

module.exports = { athenaReady, pluginLoadState, makeReadyCheck };
