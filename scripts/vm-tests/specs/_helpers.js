"use strict";

/**
 * _helpers.js — Athena spec base fixture.
 *
 * Mirrors gryphon's helper shape (~/Projects/gryphon/scripts/vm-tests/specs/_helpers.js)
 * but installs BOTH plugins (gryphon vendored + athena) per AF1.
 *
 * Specs use:
 *   const { test, expect } = require("./_helpers");
 *   test("foo", async ({ obsidian }) => {
 *     await expect(obsidian.page.locator(...)).toBeVisible();
 *   });
 *
 * The `obsidian` fixture creates a throwaway vault, launches Obsidian
 * with both plugins installed, connects Playwright via CDP, and tears
 * everything down after the test — whether it passed or failed.
 */

const { obsidianSpec } = require("@shared/argus").helpers.obsidianSpec;
const { makeReadyCheck } = require("./_ready");

const config = require("../argus.config.js");

// argus obsidianSpec (#14 readiness): it creates the vault, launches Obsidian
// over CDP, really waits for the "Trust author" prompt and accepts it, and
// hands the page over only once readyCheck passes (or throws with the load
// state). This replaces the local copy from 10e1f3ad, where isVisible's
// ignored timeout plus fixed sleeps could miss the prompt, and two Escapes
// then closed it without trusting: no plugin loaded.
const { test, expect } = obsidianSpec({
  config,
  playwrightTest: require("@playwright/test"), // the runner's own instance (OR-9)
  basePort: 9322, maxPort: 9399,               // offset from gryphon's 9222
  // A cold Electron launch on a GPU-less VM is slow; warm launches return early.
  startupTimeoutMs: 90_000,
  // Readiness has its own budget (argus default 30 s). Matrix -3 hit it with
  // gryphon not loaded; 120 s tells a slow cold load from a load that fails.
  readyTimeoutMs: 120_000,
  // athenaReady, plus each plugin's known/enabled/loaded flags and the
  // page's console errors logged to the run output (see _ready.js).
  readyCheck: makeReadyCheck(),
});

/**
 * Open Athena's chat view via its registered command, returning a
 * locator for the chat input textarea. Using the command id is
 * immune to ribbon-icon-obscured-by-modal flakiness — same rationale
 * as gryphon's openGryphonChat helper.
 *
 * Athena's open command id is "athena:open-chat" (see plugin.js:843).
 */
async function openAthenaChat(page) {
  await page.evaluate(() => {
    window.app.commands.executeCommandById("athena:open-chat");
  });
  // Athena extends GryphonChatView so the chat input retains
  // gryphon's class name (the textarea is rendered by Gryphon's
  // base class). Athena adds KB autocomplete on top — the
  // .hermes-ac / .athena-ac container appears when the user types
  // commands; not asserted in the smoke spec.
  const input = page.locator("textarea.gryphon-input");
  // 20s, not 10s: the heavy Gryphon chat view renders slower on a resource-
  // constrained guest (the Windows VM in particular).
  await expect(input).toBeVisible({ timeout: 20_000 });
  return input;
}

/**
 * Stage a minimal Athena KB skeleton into a freshly-created test vault.
 * Specs that exercise `kb stats` / `kb lint` / `kb add` need:
 *   - `bin/` copied in (the plugin spawns `<vault>/bin/kb …`)
 *   - the canonical raw/ + wiki/ category dirs created so kb's path
 *     resolution doesn't fall back to a stub
 *   - an empty inbox/ tree so `kb add` no-args has somewhere to look
 *
 * Idempotent — safe to call multiple times against the same vault.
 *
 * NOTE: this is a host-mode helper. For VM mode the same skeleton must
 * be staged inside the bundle (argus AF2 + the bundle manifest in
 * argus.config.js targets block).
 */
async function prepareKbVault(vaultPath) {
  const fs = require("fs");
  const path = require("path");
  const ATHENA_ROOT = path.resolve(__dirname, "..", "..", "..");

  // Resolve bin/ via the OR-4 resource env var the runner exports on the guest
  // (ATHENA_BIN_DIR → bundled <work>/bin), falling back to the host repo layout.
  // Without this, the guest computed `<work>/specs/../../../bin` =
  // `%LOCALAPPDATA%\bin`, so every kb spec ENOENT'd on Windows. Mirrors the
  // ATHENA_PLUGIN_DIR / GRYPHON_PROTECT_DIR pattern used by the fixture + 07.
  const ATHENA_BIN = process.env.ATHENA_BIN_DIR || path.join(ATHENA_ROOT, "bin");

  // Copy bin/ wholesale (athena's plugin spawns `<vault>/bin/kb`).
  await fs.promises.cp(
    ATHENA_BIN,
    path.join(vaultPath, "bin"),
    { recursive: true, force: true },
  );
  // Make sure the kb script is executable on POSIX (cp preserves mode
  // on most platforms, but be defensive — chmod is a no-op on Windows).
  try {
    await fs.promises.chmod(path.join(vaultPath, "bin", "kb"), 0o755);
    await fs.promises.chmod(path.join(vaultPath, "bin", "kb-capture"), 0o755);
  } catch (_) { /* best-effort */ }

  // Canonical category dirs the lint check + stats walk.
  for (const cat of ["webpages", "repos", "papers", "videos", "images", "books"]) {
    await fs.promises.mkdir(path.join(vaultPath, "raw", cat, "artifacts"), { recursive: true });
    await fs.promises.mkdir(path.join(vaultPath, "wiki", "format", cat), { recursive: true });
  }
  for (const synth of ["topics", "entities", "insights", "comparisons", "journal"]) {
    await fs.promises.mkdir(path.join(vaultPath, "wiki", synth), { recursive: true });
  }
  await fs.promises.mkdir(path.join(vaultPath, "inbox", "Clippings"), { recursive: true });

  // RULES.md is read at every kb command — empty file is fine, just must exist.
  const rulesPath = path.join(vaultPath, "RULES.md");
  try {
    await fs.promises.access(rulesPath);
  } catch {
    await fs.promises.writeFile(rulesPath, "# RULES\n\n(test vault — no rules)\n");
  }
}

/**
 * Create a fresh throwaway vault, prepare it (bin/ + canonical dirs + RULES.md),
 * and return a handle { vaultPath, cleanup }. Used by pure-shell specs that run
 * `bin/kb <cmd>` directly without booting Obsidian (06-kb-commands-full).
 *
 * This wraps prepareKbVault, which only prepares an existing directory — the
 * helper that created+returned the vault was dropped in a refactor while 06
 * still called it, so 06's beforeAll threw "createPreparedVault is not a
 * function". Restored here so the helper that prepares and the helper that
 * creates+prepares are both available.
 */
async function createPreparedVault() {
  const fs = require("fs");
  const os = require("os");
  const path = require("path");
  const vaultPath = await fs.promises.mkdtemp(path.join(os.tmpdir(), "kb-vmtest-vault-"));
  await prepareKbVault(vaultPath);
  return {
    vaultPath,
    cleanup: async () => {
      try {
        await fs.promises.rm(vaultPath, { recursive: true, force: true });
      } catch (_) { /* best-effort */ }
    },
  };
}

/**
 * Invoke an athena `kb` command via the plugin's runMechanical helper.
 * Returns { ok, stdout, stderr }.
 *
 * This is the cleanest way to exercise the kb→Python pipeline from a
 * spec: bypasses the chat UI entirely (faster + less flaky) but uses
 * the SAME spawn code path the chat UI hits via onBeforeSend.
 */
async function runKbCommand(page, command, args = []) {
  return page.evaluate(
    async ([cmd, argv]) => {
      // The plugin's onload is async; on a slow guest (notably the Windows VM)
      // a spec can call this before onload has registered the plugin or set up
      // settings. Poll up to ~12s for plugin + runMechanical + settings to be
      // ready, instead of losing the race.
      const deadline = Date.now() + 12000;
      let plugin = window.app?.plugins?.plugins?.athena;
      while ((!plugin
          || typeof plugin.runMechanical !== "function"
          || !plugin.settings) && Date.now() < deadline) {
        await new Promise((r) => setTimeout(r, 200));
        plugin = window.app?.plugins?.plugins?.athena;
      }
      if (!plugin || typeof plugin.runMechanical !== "function") {
        return { ok: false, stdout: "", stderr: "athena plugin or runMechanical not found on window.app" };
      }
      return await plugin.runMechanical(cmd, argv);
    },
    [command, args],
  );
}

module.exports = { test, expect, openAthenaChat, prepareKbVault, createPreparedVault, runKbCommand };
