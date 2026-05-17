/**
 * Athena — Obsidian plugin for capturing + synthesizing knowledge.
 *
 * Chat UI comes from GryphonChatView (vendor/gryphon/src/chat-view.js). Athena
 * configures it via composition, not inheritance — an options bag passed
 * at construction time. Extension points used:
 *   - extraToolStatus      — adds KB MCP tool status messages
 *   - extraProcessArgs     — --disable-slash-commands, --allowedTools,
 *                            --append-system-prompt
 *   - onBeforeSend         — intercepts mechanical `kb` commands before
 *                            they reach Claude Code
 *   - autocompleteSources  — registers `kb ...` completion alongside `/`
 *   - stopStreamingHooks   — aborts mechanical subprocess + browser capture
 *
 * KB features (ingest pipeline, duplicate detection, browser capture,
 * Web Clipper watcher, url-new.txt watcher, wiki page builder) live on
 * AthenaPlugin itself.
 */

const {
  Plugin, PluginSettingTab, Setting, Notice, Modal,
} = require("obsidian");
const { spawn, execFile, execFileSync } = require("child_process");
const path = require("path");
const fs = require("fs");
const os = require("os");

// Gryphon is consumed as a git submodule at vendor/gryphon. The chat UI
// is normally extended through Gryphon's documented extension points
// (options bag on GryphonChatView). One narrow exception: the welcome
// panel's hardcoded brand strings — Athena owns those via a thin
// AthenaChatView subclass that swaps text nodes after super renders.
// See src/athena/athena-chat-view.js for the architectural rationale.
const { AthenaChatView } = require("./athena-chat-view");
const {
  DEFAULT_SETTINGS, MODELS, EFFORTS, PERMS, PROVIDER_PREFS,
  resolveConnectionTimeoutMs,
} = require("../../vendor/gryphon/src/constants");
const { findClaudeBinary, buildEnhancedPath } = require("../../vendor/gryphon/src/utils");
const { SkillRegistry } = require("../../vendor/gryphon/src/skills");
// Windows-safe spawn helper for `.cmd` / `.bat` shims (npm-installed CLIs
// land as claude.cmd / codex.cmd / etc. on Windows). Node 20+ refuses to
// spawn `.cmd` directly without shell:true or windowsVerbatimArguments:true
// (CVE-2024-27980 mitigation) — bare spawn returns EINVAL. See win-spawn.js
// in Gryphon for the full quoting+escaping rationale.
const { isWindowsShim, wrapForCmdShim } = require("../../vendor/gryphon/packages/protect/src/win-spawn");

// Resolve the Python 3 executable for this OS. POSIX has `python3` on
// PATH by convention; Windows defaults to `python.exe` (no `python3.exe`
// unless the user explicitly installed it that way). Cache the lookup so
// every spawn doesn't re-scan.
let _pythonCmd = null;
function pythonCmd() {
  if (_pythonCmd) return _pythonCmd;
  if (process.platform === "win32") {
    // On Windows, prefer plain `python` (the canonical install). The
    // Windows Python launcher `py.exe` accepts `-3` as an arg form but
    // we don't want the indirection — if `python` isn't on PATH, the
    // spawn will ENOENT with a message that points the user at install.
    _pythonCmd = "python";
  } else {
    _pythonCmd = "python3";
  }
  return _pythonCmd;
}

// Resolve a path to one of Athena's bundled Python sources. As of 1.0.9
// the build copies bin/lib/ + bin/config/ into the plugin's install
// directory so the JS plugin can spawn them without requiring the user
// to clone the full Athena vault. Fallback path (vault root) preserves
// the old "vault IS the Athena source tree" workflow for dev vaults
// and for users who used the --full-vault flag in earlier releases.
//
// `plugin` argument is the Athena plugin instance (we need this.manifest
// and this.app.vault.adapter.basePath). Returns an absolute path that
// is guaranteed to exist if either source location has the file, OR
// returns the plugin-dir path (which may not exist) as a deterministic
// default — the spawn will then ENOENT and the caller surfaces the
// honest "synthesis was skipped" message.
function resolvePythonScript(plugin, relPath) {
  const vaultPath = plugin.app.vault.adapter.basePath;
  // Plugin's install dir. manifest.dir is documented to be a vault-relative
  // path on most Obsidian versions; treat it as such with a defensive fallback.
  const pluginRel = (plugin.manifest && plugin.manifest.dir)
    || `.obsidian/plugins/${(plugin.manifest && plugin.manifest.id) || "athena"}`;
  const pluginAbs = path.isAbsolute(pluginRel)
    ? pluginRel
    : path.join(vaultPath, pluginRel);
  const inPlugin = path.join(pluginAbs, relPath);
  if (fs.existsSync(inPlugin)) return inPlugin;
  // Fallback: vault-root layout (Athena dev vault or pre-1.0.9
  // --full-vault deploys). If neither location has the file, return
  // the plugin-dir path so the error message is deterministic.
  const inVault = path.join(vaultPath, relPath);
  if (fs.existsSync(inVault)) return inVault;
  return inPlugin;
}
const { TOOL_STATUS_KB } = require("./kb-constants");
const {
  KB_COMMANDS, STATUS_MAP, DONE_STATUS_MAP, detectMechanicalCommand,
} = require("./kb-commands");

const VIEW_TYPE = "athena-view";
const ICON = "brain";

// ── Athena-specific CLI args for the persistent Claude process ─────

const ALLOWED_TOOLS = [
  "Bash", "Read", "Write", "Edit", "Glob", "Grep",
  "WebFetch", "WebSearch",
  "mcp__athena__kb_add", "mcp__athena__kb_add_content",
  "mcp__athena__kb_create", "mcp__athena__kb_export",
  "mcp__athena__kb_index", "mcp__athena__kb_insight",
  "mcp__athena__kb_journal", "mcp__athena__kb_lint", "mcp__athena__kb_list",
  "mcp__athena__kb_merge", "mcp__athena__kb_move",
  "mcp__athena__kb_purge", "mcp__athena__kb_query",
  "mcp__athena__kb_reflect", "mcp__athena__kb_remove",
  "mcp__athena__kb_rename", "mcp__athena__kb_search",
  "mcp__athena__kb_stats", "mcp__athena__kb_trash",
  "mcp__athena__kb_undo", "mcp__athena__kb_ungroup",
];

const ATHENA_SYSTEM_PROMPT =
  "You are running inside the Athena vault via the Athena Obsidian plugin. " +
  "This IS the Athena session. Follow the rules in CLAUDE.md. " +
  "IMPORTANT RULES FOR THIS SESSION: " +
  "1) Wiki pages in wiki/format/ are the AUTHORITATIVE source. Always check them before raw files. " +
  "Never report a page as auth-blocked or thin without checking if a wiki page exists for that URL. " +
  "2) When the user asks to analyze, research, update, or compare content, SEARCH for relevant existing " +
  "insight/analysis pages first (wiki/insights/, wiki/comparisons/). If you find a match, ask the user: " +
  "'I found [[Page Name]] \u2014 should I update this, or create a new analysis?' " +
  "If no existing page matches, offer to create a new insight page. " +
  "3) Use [[wikilinks]] for all page references so they are clickable in Obsidian. " +
  "4) WIKI PAGE SUMMARIES: when creating or updating a wiki page, the `summary` " +
  "frontmatter field MUST be 200-400 characters (2-3 sentences max), self-contained " +
  "and scannable. The summary is rendered in dashboards (Recently Added, Browse by " +
  "Tag, etc.) where space is constrained. Long descriptions belong in the body, not " +
  "the summary. NEVER let summary exceed 500 characters \u2014 the page-builder's " +
  "1500-char hard cap is a safety net, not a target. Short summaries make the " +
  "knowledge base scannable; long summaries make dashboards unusable.";

// ── Athena autocomplete source ─────────────────────────────────────
//
// Plugs into GryphonChatView's autocomplete-source registry. Core handles
// "/" input before this source is consulted, so this only fires on `kb ...`.
// Matches on startsWith OR substring — users often remember a word from
// the command without the leading `kb`.

const athenaKbAutocompleteSource = {
  name: "athena-kb",
  matches: (text) => text.toLowerCase().startsWith("kb"),
  suggest: (text) => {
    const query = text.toLowerCase();
    return KB_COMMANDS.filter((c) =>
      c.cmd.toLowerCase().startsWith(query) || c.cmd.toLowerCase().includes(query)
    );
  },
};

// ── AthenaPlugin — KB orchestration lives here ─────────────────────

class AthenaPlugin extends Plugin {
  async onload() {
    console.log("[athena] Plugin loaded \u2014 version", (this.manifest && this.manifest.version) || "?");

    // Mutual exclusivity: Athena includes all Gryphon features, disable
    // Gryphon if enabled. Use disablePluginAndSave so the change persists
    // across restarts — disablePlugin alone is in-memory only and Gryphon
    // would re-enable on the next Obsidian launch.
    if (this.app.plugins.enabledPlugins.has("gryphon")) {
      const plugins = this.app.plugins;
      const disableFn = plugins.disablePluginAndSave
        ? plugins.disablePluginAndSave.bind(plugins)
        : plugins.disablePlugin.bind(plugins);
      try {
        await disableFn("gryphon");
        // Confirm the disable actually took effect before claiming so.
        if (plugins.enabledPlugins.has("gryphon")) {
          console.warn("[athena] disable returned but Gryphon is still in enabledPlugins");
          new Notice(
            "Athena: could not disable Gryphon automatically. " +
            "Please disable it manually in Settings \u2192 Community plugins.",
            8000
          );
        } else {
          new Notice(
            "Athena includes Gryphon features \u2014 Gryphon has been disabled.",
            5000
          );
        }
      } catch (e) {
        console.warn("[athena] could not disable gryphon plugin:", e && e.message);
        new Notice(
          "Athena: could not disable Gryphon automatically. " +
          "Please disable it manually in Settings \u2192 Community plugins.",
          8000
        );
      }
    }

    await this.loadSettings();

    this.skillRegistry = new SkillRegistry(this.app);
    this.skillRegistry.init().catch((e) =>
      console.warn("[athena] SkillRegistry init failed:", e)
    );

    // Ensure each configured Web Clipper target directory exists, then
    // attach a watcher to each one. Paths come from a comma-separated
    // setting so users can cover both Obsidian Web Clipper's factory
    // default (`clippings/` at vault root) and any legacy or custom
    // location simultaneously.
    this._clipWatchers = [];
    for (const clipDir of this._resolveClipDirs()) {
      try {
        if (!fs.existsSync(clipDir)) fs.mkdirSync(clipDir, { recursive: true });
      } catch {}
      this._setupClipWatcher(clipDir);
    }

    // Ensure Athena's three-layer dir structure exists in the vault.
    // The Python backend creates these on first kb run, but Community-
    // Plugins / BRAT users start from a vault that has none of them. A
    // browser-captured raw file would otherwise hit ENOENT on write,
    // surfacing as a confusing "KB command error" in chat. Belt-and-
    // suspenders: each writeFileSync in the ingest path also calls
    // mkdirSync({recursive:true}) before writing, so even if a category
    // is missed here the write still succeeds.
    const vaultRoot = this.app.vault.adapter.basePath;
    for (const sub of [
      "raw/webpages/artifacts",
      "raw/papers/artifacts",
      "raw/repos/artifacts",
      "raw/videos/artifacts",
      "inbox",
    ]) {
      try {
        const dir = path.join(vaultRoot, sub);
        if (!fs.existsSync(dir)) fs.mkdirSync(dir, { recursive: true });
      } catch {}
    }
    // 1.0.10: create inbox/url-resolved.tsv as empty on first run if
    // missing. Read sites inside the ingest path catch ENOENT, but the
    // catch logs `[athena] ingest: url-resolved update failed: ENOENT...`
    // every time — noise that masks real failures. Same pattern as 1.0.5
    // for url-new.txt.
    try {
      const tsv = path.join(vaultRoot, "inbox", "url-resolved.tsv");
      if (!fs.existsSync(tsv)) fs.writeFileSync(tsv, "");
    } catch {}

    // Watch inbox/url-new.txt for new URLs
    this._setupUrlNewWatcher();

    // First-run setup wizard
    if (!this.settings._setupComplete) {
      const cp = this.settings.claudePath || findClaudeBinary();
      if (!cp) {
        new Notice("Athena: Claude Code CLI not found. Set the path in Settings > Athena.", 10000);
      }
      const vaultName = this.app.vault.getName();
      new AthenaSetupWizard(this.app, vaultName).open();
      this.settings._setupComplete = true;
      await this.saveSettings();
    }

    // Register the view — composition for behavior, thin subclass for
    // brand text. Athena configures the chat view through the options
    // bag (Gryphon's documented extension API) and uses AthenaChatView
    // (subclass of GryphonChatView) solely to swap welcome-panel
    // strings — see src/athena/athena-chat-view.js. Extension points:
    //   autocompleteSources — adds `kb ...` completions next to core's `/`
    //   stopStreamingHooks  — kills mechanical subprocess + browser capture
    this.registerView(VIEW_TYPE, (leaf) => {
      // extraProcessArgs are Claude-Code-CLI-specific flags. Other providers
      // (Codex CLI, Gemini CLI, *-api) reject these and the spawn fails.
      // Only pass them when Claude Code is the active provider. See #121.
      // For non-Claude providers, Athena loses the safety guardrails that
      // these flags express (allowedTools allowlist, system-prompt append).
      // That's a real loss; tracked in #39 (Gryphon-side intent translation).
      const provider = this.settings.providerPreference || "auto";
      const usesClaudeCodeCLI = provider === "claude-code" || provider === "auto";
      const claudeCodeArgs = usesClaudeCodeCLI
        ? [
            "--disable-slash-commands",
            "--allowedTools", ...ALLOWED_TOOLS,
            "--append-system-prompt", ATHENA_SYSTEM_PROMPT,
          ]
        : [];
      return new AthenaChatView(leaf, this, {
        viewType: VIEW_TYPE,
        displayText: "Athena",
        icon: ICON,
        extraToolStatus: TOOL_STATUS_KB,
        extraProcessArgs: claudeCodeArgs,
        onBeforeSend: (text) => this._handleKbCommand(text),
        autocompleteSources: [athenaKbAutocompleteSource],
        stopStreamingHooks: [
          (view) => {
            if (view._mechanicalProc) {
              try { view._mechanicalProc.kill("SIGTERM"); } catch {}
              view._mechanicalProc = null;
            }
          },
          () => this.abortBrowserCapture(),
        ],
      });
    });

    this.addRibbonIcon(ICON, "Open Athena", () => this.activateView());
    this.addRibbonIcon("search", "Athena Search", () => {
      new SearchModal(this.app, this.app.vault.adapter.basePath).open();
    });

    this.addCommand({ id: "open-chat", name: "Open chat", callback: () => this.activateView() });

    // Mirrors Gryphon's hotkey path. Uses `callback` (not `editorCallback`)
    // so the command is available from Reading mode too. Cascades through
    // three selection sources — see _pickSelectionForInjection.
    this.addCommand({
      id: "quote-highlight-into-chat",
      name: "Quote highlighted text into chat",
      callback: async () => {
        const picked = this._pickSelectionForInjection();
        if (!picked) {
          new Notice("Athena: no text selected");
          return;
        }
        await this.activateView();
        const leaves = this.app.workspace.getLeavesOfType(VIEW_TYPE);
        const chatView = leaves[0] && leaves[0].view;
        if (chatView && typeof chatView.insertSelectionIntoInput === "function") {
          chatView.insertSelectionIntoInput(picked.text, picked.file);
        } else {
          new Notice("Athena: chat view not available");
        }
      },
    });

    this.addCommand({
      id: "open-inbox",
      name: "Open URL inbox",
      callback: async () => {
        const mdPath = "inbox/Add URLs.md";
        const txtPath = "inbox/url-new.txt";
        let file = this.app.vault.getAbstractFileByPath(mdPath);
        if (!file) {
          const header = `Paste URLs below (one per line). Athena processes them automatically.\n\n---\n\n`;
          let existing = "";
          try { existing = fs.readFileSync(path.join(this.app.vault.adapter.basePath, txtPath), "utf8").trim(); } catch {}
          await this.app.vault.create(mdPath, header + existing);
          file = this.app.vault.getAbstractFileByPath(mdPath);
        }
        if (file) {
          const leaf = this.app.workspace.getLeaf(false);
          await leaf.openFile(file);
        }
      },
    });

    // Sync: when Add URLs.md is modified, extract URLs to url-new.txt
    this.registerEvent(this.app.vault.on("modify", (file) => {
      if (file.path === "inbox/Add URLs.md") {
        setTimeout(async () => {
          try {
            const content = await this.app.vault.read(file);
            const parts = content.split("---");
            const urlSection = parts.length > 1 ? parts.slice(1).join("---") : content;
            // Sanitize URLs against markdown syntax leaks. Users routinely
            // paste URLs from rendered markdown / formatted post text where
            // the URL is wrapped in **bold**, [link](url), `code`, etc.
            // Without stripping, the URL kept in url-new.txt becomes
            // "https://lnkd.in/X**" or "[https://x](https://x)" — kb-capture
            // then fetches the malformed URL, gets a 404 / interstitial,
            // and creates a sparse wiki page with garbage title and URL.
            // Bug class surfaced 0.10.16 (Discord/decodingtrust/arxiv sparse pages).
            const sanitizeUrl = (raw) => {
              let u = raw.trim();
              // Strip markdown link form: [https://x](https://x) → https://x
              const linkForm = u.match(/^\[(https?:\/\/[^\]]+)\]\((https?:\/\/[^)]+)\)/);
              if (linkForm) {
                u = linkForm[2];  // prefer the parenthesized URL (the actual target)
              }
              // Strip surrounding parens/brackets/braces/backticks/quotes
              u = u.replace(/^[\[\(\{`'"<]+|[\]\)\}`'">]+$/g, "");
              // Strip trailing markdown emphasis (bold/italic) and punctuation
              // Common forms: **, __, *, _, ., ,, ;, !, ?, …
              while (u.length && /[\*_.,;!?…]$/.test(u)) {
                u = u.slice(0, -1);
              }
              return u;
            };
            const urls = urlSection
              .split("\n")
              .map(sanitizeUrl)
              .filter(l => l.startsWith("http"));
            if (urls.length > 0) {
              const txtPath = path.join(this.app.vault.adapter.basePath, "inbox", "url-new.txt");
              fs.writeFileSync(txtPath, urls.join("\n") + "\n");
              const header = content.split("---")[0] + "---\n\n";
              await this.app.vault.modify(file, header);
            }
          } catch (e) { console.log("[athena] Add URLs sync error:", e.message); }
        }, 2000);
      }
    }));

    this.addCommand({
      id: "new-session",
      name: "New session",
      callback: () => {
        const leaves = this.app.workspace.getLeavesOfType(VIEW_TYPE);
        if (leaves.length > 0) {
          leaves[0].view.handleChatCommand("/clear");
          this.app.workspace.revealLeaf(leaves[0]);
        } else {
          this.activateView();
        }
      },
    });

    // kb commands in Obsidian command palette
    const sendKbCommand = async (cmd) => {
      await this.activateView();
      const leaves = this.app.workspace.getLeavesOfType(VIEW_TYPE);
      if (leaves.length > 0) {
        const view = leaves[0].view;
        view.inputEl.value = cmd;
        view.sendMessage();
      }
    };

    const paletteCommands = [
      { id: "kb-stats",        name: "KB: Show stats",              cmd: "kb stats" },
      { id: "kb-lint",         name: "KB: Health check (lint)",     cmd: "kb lint" },
      { id: "kb-list",         name: "KB: List all pages",          cmd: "kb list" },
      { id: "kb-list-topics",  name: "KB: List topics",             cmd: "kb list --topics" },
      { id: "kb-list-insights",name: "KB: List insights",           cmd: "kb list --insights" },
      { id: "kb-list-projects",name: "KB: List projects",           cmd: "kb list --projects" },
      { id: "kb-list-recent",  name: "KB: Recently added",          cmd: "kb list --recent" },
      { id: "kb-search",       name: "KB: Search (chat)",           cmd: "kb search " },
      { id: "kb-rules",        name: "KB: Show processing rules",   cmd: "kb rules" },
      { id: "kb-reflect",      name: "KB: Reflect on journal",      cmd: "kb reflect" },
      { id: "kb-index",        name: "KB: Rebuild search index",    cmd: "kb index" },
    ];

    for (const pc of paletteCommands) {
      this.addCommand({
        id: pc.id,
        name: pc.name,
        callback: () => {
          if (pc.cmd.endsWith(" ")) {
            this.activateView().then(() => {
              const leaves = this.app.workspace.getLeavesOfType(VIEW_TYPE);
              if (leaves.length > 0) {
                const view = leaves[0].view;
                view.inputEl.value = pc.cmd;
                view.inputEl.focus();
              }
            });
          } else {
            sendKbCommand(pc.cmd);
          }
        },
      });
    }

    this.addCommand({
      id: "search-modal",
      name: "Search knowledge base",
      callback: () => new SearchModal(this.app, this.app.vault.adapter.basePath).open(),
    });

    this.addCommand({
      id: "setup-wizard",
      name: "Setup wizard (Web Clipper configuration)",
      callback: () => {
        new AthenaSetupWizard(this.app, this.app.vault.getName()).open();
      },
    });

    this.addSettingTab(new AthenaSettingTab(this.app, this));

    // Watchdog — initial check shortly after UI loads + periodic
    setTimeout(() => this._watchdogCheck(), 5000);
    this._watchdogInterval = setInterval(() => this._watchdogCheck(), 60000);
  }

  async onunload() {
    // Abort any in-flight browser capture so hidden webview/BrowserWindow is cleaned up
    if (typeof this.abortBrowserCapture === "function") {
      try { this.abortBrowserCapture(); } catch {}
    }
    for (const w of (this._clipWatchers || [])) {
      try { w.watcher.close(); } catch {}
    }
    this._clipWatchers = [];
    if (this._urlNewWatcher) { try { this._urlNewWatcher.close(); } catch {} }
    if (this._urlNewTimer) clearTimeout(this._urlNewTimer);
    if (this._watchdogInterval) clearInterval(this._watchdogInterval);
    for (const leaf of this.app.workspace.getLeavesOfType(VIEW_TYPE)) {
      if (leaf.view.claudeProcess) leaf.view.claudeProcess.abort();
    }
    if (this.skillRegistry) this.skillRegistry.unload();
  }

  async activateView() {
    const { workspace } = this.app;
    let leaf = workspace.getLeavesOfType(VIEW_TYPE)[0];
    if (!leaf) {
      const nl = this.settings.openInMainTab ? workspace.getLeaf("tab") : workspace.getRightLeaf(false);
      if (nl) { await nl.setViewState({ type: VIEW_TYPE, active: true }); leaf = nl; }
    }
    if (leaf) {
      workspace.revealLeaf(leaf);
      requestAnimationFrame(() => { if (leaf.view.inputEl) leaf.view.inputEl.focus(); });
    }
  }

  /**
   * Cascade through selection sources for the "insert selection" command.
   * Order: chat view's cached selection → active editor selection →
   * current window DOM selection. First hit wins. Mirrors the same
   * helper on GryphonPlugin; kept symmetric so both plugins behave the
   * same way. Returns {text, file} or null.
   */
  _pickSelectionForInjection() {
    const leaves = this.app.workspace.getLeavesOfType(VIEW_TYPE);
    const viewCache = leaves[0] && leaves[0].view && leaves[0].view._cachedSelection;
    if (viewCache && viewCache.text) {
      return { text: viewCache.text, file: viewCache.file };
    }
    const { MarkdownView } = require("obsidian");
    const mdView = this.app.workspace.getActiveViewOfType(MarkdownView);
    if (mdView && mdView.editor) {
      const sel = mdView.editor.getSelection();
      if (sel) return { text: sel, file: mdView.file || null };
    }
    const winSel = document.getSelection();
    if (winSel && !winSel.isCollapsed) {
      const text = winSel.toString();
      if (text) return { text, file: this.app.workspace.getActiveFile() };
    }
    return null;
  }

  async loadSettings() {
    this.settings = Object.assign({}, DEFAULT_SETTINGS, await this.loadData());
    // Athena has its own guardrails (--allowedTools whitelist + system
    // prompt + scoped MCP). Gryphon's protected-mode IPC infrastructure
    // (GRYPHON_PERMISSION_SOCKET, hook-settings.json, IPC server lifecycle)
    // is not wired up on AthenaPlugin, so leaving protectedMode ON would
    // cause the CC provider to spawn with a dead socket path and fail.
    // Force these off here every load — saving back to disk so a future
    // toggle in Gryphon Settings (if Gryphon is ever re-enabled separately)
    // doesn't drift Athena's runtime back into a broken state.
    this.settings.protectedMode = false;
    this.settings.protectedPathsEnabled = false;
    this.settings.protectedCommandsEnabled = false;
  }

  async saveSettings() {
    await this.saveData(this.settings);
    // Issue #132: notify the GryphonChatView mounted inside Athena's panel
    // that settings changed, so it can refresh the toolbar model/effort/
    // permission badges. Gryphon-as-a-plugin fires this event from its own
    // saveSettings (#40), but Athena is a consumer with its OWN settings
    // tab + saveSettings — without this, Athena's panel badges stay frozen
    // at the values present when the view was first opened.
    if (this.app && this.app.workspace && typeof this.app.workspace.trigger === "function") {
      this.app.workspace.trigger("gryphon:settings-changed", this.settings);
    }
  }

  // GryphonChatView's send pipeline calls plugin.ensureIpcListening before
  // every spawn to confirm the permission-classification IPC server is up.
  // Athena doesn't run that server (see loadSettings — protected-mode is
  // disabled), so we return true to let chat-view proceed without firing
  // the "guardrail IPC offline" notice. Returning false would also work
  // (the notice is gated on providerPreference === "claude-code" and
  // Athena defaults to "auto"), but true is the honest answer for
  // "is the permission server in a state that won't break the spawn?"
  // — yes, because we configured the spawn to not use it.
  async ensureIpcListening(_timeoutMs) {
    return true;
  }

  _getActiveView() {
    const leaves = this.app.workspace.getLeavesOfType(VIEW_TYPE);
    return leaves.length > 0 ? leaves[0].view : null;
  }

  // ── onBeforeSend hook: intercept mechanical KB commands ──────────

  _handleKbCommand(text) {
    const mechanical = detectMechanicalCommand(text);
    if (!mechanical) return false;

    const view = this._getActiveView();
    if (!view) return false;  // no view to render into — let it pass through

    // Dispatch async — don't await. Hook must return synchronously.
    this._runKbCommandAsync(text, mechanical, view).catch((e) => {
      console.error("[athena] KB command error:", e);
      try {
        view.addSystemMessage("KB command error: " + (e && e.message ? e.message : String(e)));
      } catch {}
      view.isStreaming = false;
      // Per Gryphon issue #3: input stays enabled while streaming. Don't
      // re-disable here either — Gryphon's queue logic gates new sends
      // off view.isStreaming alone.
    });

    return true;  // consumed
  }

  async _runKbCommandAsync(text, mechanical, view) {
    view.addUserMessage(text, "mechanical");
    view.isStreaming = true;
    // Per Gryphon issue #3: don't disable inputEl. Gryphon's queue
    // logic gates new sends off view.isStreaming so the user can type
    // and queue a follow-up while a KB command runs.

    try {
      // kb add: capture is mechanical ($0)
      if (mechanical.command === "add") {
        console.log("[athena] kb add start", { args: mechanical.args });
        view.startStreamingMessage();
        const hasUrl = mechanical.args.length > 0 && mechanical.args[0];

        if (!hasUrl) {
          await this._kbAddNoArgs(view);
          view.addCostInfo(0, null);
          if (view.inputEl) view.inputEl.focus();
          return;
        }

        // URL-specific add
        const url = mechanical.args[0];
        const result = await this.ingestContent({ url, source: "kb-add", view });

        if (result.status === "duplicate") {
          view.finalizeStreamingMessage(
            `**Already in knowledge base:** [[${result.dupPage}]]\n\n` +
            `Matched by ${result.dupMethod}.\n` +
            `To update with newer content, say "update [[${result.dupPage}]]"`,
            `Already captured: ${result.dupPage}`
          );
        } else if (result.status === "updated") {
          view.finalizeStreamingMessage(
            `**Updated:** [[${result.pageName}]]\n\nRaw content and wiki page refreshed with better content.` +
            (result.summary ? `\n\n${result.summary}` : ""),
            `Updated: ${result.pageName}`
          );
        } else if (result.status === "failed") {
          if (result.summary && result.summary.includes("authentication")) {
            view.finalizeStreamingMessage(
              `Could not capture full content (requires authentication).\n\n` +
              `**Options:**\n` +
              `1. Open the link in your browser, use Web Clipper, then \`kb add\`\n` +
              `2. Copy the text and paste it here\n` +
              `3. Say "skip" to move on`
            );
          } else {
            view.finalizeStreamingMessage(result.summary || "Capture failed.", result.summary || "Capture failed");
          }
        } else {
          // created
          const lines = [];
          // Detect the capture-only (no Python backend) case so the user
          // gets an honest message about what actually happened, instead
          // of the vague "Page added to knowledge base." we used to show
          // when synthesis silently failed on fresh-vault installs.
          // 1.0.9+: checks plugin-bundled location first, then vault-side.
          // resolvePythonScript returns the plugin-dir path even when neither
          // exists, so we wrap with fs.existsSync to detect the truly-absent
          // case (Python lib missing → capture-only message fires).
          const pyBackend = fs.existsSync(
            resolvePythonScript(this, "bin/lib/wiki_page.py")
          );
          if (result.pageName) {
            lines.push(`**Captured:** ${url}`);
            lines.push(`**Page created:** [[${result.pageName}]]`);
            if (result.summary) lines.push(`\n${result.summary}`);
          } else if (!pyBackend) {
            lines.push(`**Raw saved:** ${url}`);
            lines.push("");
            lines.push("Wiki synthesis was skipped — Athena's Python backend isn't installed in this vault. Capture-only mode is what Community Plugins users currently get.");
            lines.push("");
            lines.push("For full wiki synthesis, clone the Athena vault from https://github.com/polleoai/athena and open it in Obsidian instead.");
          } else {
            // Python backend IS present but produced no pageName. That
            // means wiki_page.py ran but failed (OSError, schema error,
            // pydantic error, etc.) — the previous "Page added to
            // knowledge base." was a lie that hid real failures. Now
            // surface this honestly so the user knows to check the
            // console for the actual error. Common 1.0.x cause: titles
            // with chars that are valid on macOS but invalid on Windows
            // (the `:`-in-filename bug fixed in 1.0.11).
            lines.push(`**Raw saved:** ${url}`);
            lines.push("");
            lines.push("Wiki synthesis ran but did not return a page name. The raw file was saved, but no wiki page was created — likely a Python error from `wiki_page.py`. Open the dev console (Ctrl+Shift+I) and look for `[athena] wiki_page.py stderr:` for the actual error.");
          }
          view.finalizeStreamingMessage(
            lines.join("\n"),
            result.pageName ? `Page created: ${result.pageName}`
              : (!pyBackend ? "Raw saved (capture-only mode)" : "Raw saved (synthesis error)")
          );
        }
        view.addCostInfo(0, null);
        if (view.inputEl) view.inputEl.focus();
        return;
      }

      // All other kb commands: run `bin/kb <cmd> [args]` and format output
      view.startStreamingMessage();
      view.updateStatus(STATUS_MAP[mechanical.command] || "Processing...");
      const result = await this.runMechanical(mechanical.command, mechanical.args, null, view);
      const stdout = (result.stdout || "").trim();
      const stderr = (result.stderr || "").trim();

      if (!result.ok) {
        view.finalizeStreamingMessage(stderr || stdout || "Command failed.");
        view.addCostInfo(0, null);
        if (view.inputEl) view.inputEl.focus();
        return;
      }

      let output = stdout;
      switch (mechanical.command) {
        case "lint":
        case "stats":
        case "rules":
          // Already user-friendly
          break;
        case "search":
          if (!stdout) output = "No results found.";
          break;
        case "index":
          output = stdout || "Search index rebuilt.";
          break;
        case "journal": {
          const entryMatch = stdout.match(/Created:\s*(.+\.md)/);
          output = entryMatch
            ? `**Journal entry saved:** [[${entryMatch[1].replace(/\.md$/, "").split("/").pop()}]]`
            : stdout || "Journal entry saved.";
          break;
        }
        case "undo":
          output = stdout || "Nothing to undo.";
          break;
        case "purge":
          output = stdout || "Trash is empty \u2014 nothing to purge.";
          break;
        case "trash":
          output = stdout || "Trash is empty.";
          break;
        case "list":
          if (!stdout) output = "No pages found.";
          break;
        default:
          output = stdout || "Done.";
      }
      view.finalizeStreamingMessage(output, DONE_STATUS_MAP[mechanical.command] || "Done");
      view.addCostInfo(0, null);
      if (view.inputEl) view.inputEl.focus();
    } finally {
      view.isStreaming = false;
      // Per Gryphon issue #3: input stays enabled throughout. Drain any
      // prompts the user queued while this KB command was running so
      // they fire against the now-idle session.
      if (typeof view._drainQueuedPrompts === "function") view._drainQueuedPrompts();
    }
  }

  /** `kb add` with no args: retry failed + scan for orphan raw files. */
  async _kbAddNoArgs(view) {
    const vaultPath = this.app.vault.adapter.basePath;
    const output = [];
    let created = 0;

    // Step 1: Retry "Needs Attention" URLs from url-resolved.tsv
    view.updateStatus("Checking for unprocessed URLs...");
    const tsvPath = path.join(vaultPath, "inbox", "url-resolved.tsv");
    try {
      const tsv = fs.readFileSync(tsvPath, "utf8");
      const needsRetry = [];
      for (const line of tsv.split("\n")) {
        const parts = line.split("\t");
        if (parts.length >= 3 && (parts[0] === "uncapturable" || parts[0] === "thin")) {
          needsRetry.push({ url: parts[2].trim(), title: parts[1] });
        }
      }
      if (needsRetry.length > 0) {
        view.updateStatus(`Retrying ${needsRetry.length} previously failed URLs...`);
        for (const entry of needsRetry) {
          view.updateStatus(`Processing ${entry.title || entry.url.substring(0, 40)}...`);
          const result = await this.ingestContent({ url: entry.url, source: "retry", view });
          if (result.status === "created" || result.status === "updated") {
            created++;
            output.push(`**Captured:** [[${result.pageName}]]`);
          }
        }
      }
    } catch (e) { console.log("[athena] retry scan failed:", e.message); }

    // Step 2: Orphan raw-file scan via pipeline
    view.updateStatus("Checking for orphan raw files...");
    try {
      const referencedRaw = new Set();
      let wikiContentIndex = "";
      const wikiDirs = ["wiki/format/webpages", "wiki/format/repos", "wiki/format/papers",
                        "wiki/format/videos", "wiki/format/images", "wiki/topics", "wiki/insights"];
      for (const wd of wikiDirs) {
        const fullWd = path.join(vaultPath, wd);
        if (!fs.existsSync(fullWd)) continue;
        for (const wf of fs.readdirSync(fullWd)) {
          if (!wf.endsWith(".md")) continue;
          try {
            const content = fs.readFileSync(path.join(fullWd, wf), "utf8");
            const rpMatch = content.substring(0, 500).match(/raw_path:\s*"?(\S+?)"?\s*$/m);
            if (rpMatch) referencedRaw.add(rpMatch[1]);
            wikiContentIndex += content.substring(0, 5000) + "\n";
          } catch {}
        }
      }
      for (const rd of ["raw/webpages/artifacts", "raw/repos/artifacts", "raw/papers/artifacts", "raw/videos/artifacts"]) {
        const fullRd = path.join(vaultPath, rd);
        if (!fs.existsSync(fullRd)) continue;
        for (const rf of fs.readdirSync(fullRd)) {
          if (!rf.endsWith(".md") || rf.startsWith("_")) continue;
          const relPath = rd + "/" + rf;
          const slug = rf.replace(".md", "");
          if (referencedRaw.has(relPath)) continue;
          if (wikiContentIndex.includes(slug)) continue;
          const rfPath = path.join(fullRd, rf);
          let rfContent = "";
          try { rfContent = fs.readFileSync(rfPath, "utf8"); } catch { continue; }
          const urlMatch = rfContent.match(/\*\*URL:\*\*\s*(https?:\/\/\S+)/) ||
                           rfContent.match(/^source:\s*['"]?(https?:\/\/\S+?)['"]?\s*$/m) ||
                           rfContent.match(/^url:\s*['"]?(https?:\/\/\S+?)['"]?\s*$/m);
          const rfUrl = urlMatch ? urlMatch[1] : null;
          if (rfUrl && wikiContentIndex.includes(rfUrl)) continue;
          if (rfUrl) {
            const ytMatch = rfUrl.match(/(?:v=|youtu\.be\/)([a-zA-Z0-9_-]{11})/);
            if (ytMatch && wikiContentIndex.includes(ytMatch[1])) continue;
          }
          view.updateStatus(`Processing orphan: ${rf.replace(".md", "").substring(0, 40)}...`);
          const result = await this.ingestContent({
            url: rfUrl, content: rfContent, rawPath: relPath, source: "orphan", view,
          });
          if (result.status === "created") {
            created++;
            output.push(`**Page created:** [[${result.pageName}]]`);
          }
        }
      }
    } catch (e) { console.log("[athena] orphan scan error:", e.message); }

    const finalOutput = output.filter(Boolean).join("\n");
    view.finalizeStreamingMessage(
      finalOutput || "Nothing to process.",
      created > 0 ? `${created} page${created > 1 ? "s" : ""} processed` : "Nothing to process"
    );
  }

  // ── Mechanical shell command runner (bin/kb) ──────────────────────

  // Issue #133 (continued): runs a `bin/kb` mechanical command with an
  // *idle* timeout instead of a total-wallclock one. The watchdog resets
  // every time the child writes to stdout or stderr — so a long-but-alive
  // kb operation (e.g. `kb add` doing a multi-step LLM ingest) keeps
  // running as long as it's producing progress lines, but a wedged child
  // (no output for `idleTimeoutMs`) is killed.
  //
  // Budget is resolved from settings.connectionTimeoutMs via Gryphon's
  // resolveConnectionTimeoutMs, so the same single knob ("Connection
  // timeout" in Settings → Athena) governs both Gryphon chat and Athena
  // mechanical commands. Pass an explicit `idleTimeoutMs` only when a
  // call site needs to override the resolved default (rare).
  runMechanical(command, args, idleTimeoutMs = null, view = null) {
    const vaultPath = this.app.vault.adapter.basePath;
    const kbPath = path.join(vaultPath, "bin", "kb");
    const effectiveMs = (typeof idleTimeoutMs === "number" && idleTimeoutMs > 0)
      ? idleTimeoutMs
      : resolveConnectionTimeoutMs({
          override: this.settings.connectionTimeoutMs,
          model: this.settings.model,
        });
    return new Promise((resolve) => {
      const proc = spawn(kbPath, [command, ...args], {
        cwd: vaultPath,
        env: { ...process.env, PATH: buildEnhancedPath() },
        stdio: ["pipe", "pipe", "pipe"],
      });
      if (view) view._mechanicalProc = proc;
      let stdout = "", stderr = "";
      let resolved = false;
      let timer = null;

      const armTimer = () => {
        if (timer) clearTimeout(timer);
        timer = setTimeout(() => {
          if (resolved) return;
          resolved = true;
          try { proc.kill("SIGTERM"); } catch {}
          if (view) view._mechanicalProc = null;
          resolve({
            ok: false,
            stdout: stdout.trim(),
            stderr: "No output from `kb " + command + "` for "
              + Math.round(effectiveMs / 1000) + "s (idle timeout). "
              + "Raise Settings → Athena → Connection timeout if your runs need longer.",
          });
        }, effectiveMs);
      };
      armTimer();

      proc.stdout.on("data", (d) => { stdout += d.toString(); armTimer(); });
      proc.stderr.on("data", (d) => { stderr += d.toString(); armTimer(); });
      proc.on("close", (code) => {
        if (!resolved) {
          resolved = true;
          if (timer) clearTimeout(timer);
          if (view) view._mechanicalProc = null;
          resolve({ ok: code === 0, stdout: stdout.trim(), stderr: stderr.trim() });
        }
      });
      proc.on("error", (err) => {
        if (!resolved) {
          resolved = true;
          if (timer) clearTimeout(timer);
          if (view) view._mechanicalProc = null;
          resolve({ ok: false, stdout: "", stderr: err.message });
        }
      });
    });
  }

  // ── Content Pre-processor (strip Web Clipper YAML) ────────────────

  _preprocessContent(rawContent) {
    if (!rawContent) return { body: "", url: null, title: null, description: null };

    let body = rawContent;
    let url = null, title = null, description = null;

    const yamlMatch = rawContent.match(/^---\n([\s\S]*?)\n---\n?([\s\S]*)$/);
    if (yamlMatch) {
      const yaml = yamlMatch[1];
      body = yamlMatch[2].trim();

      const urlMatch = yaml.match(/^source:\s*['"]?(https?:\/\/\S+?)['"]?\s*$/m) ||
                       yaml.match(/^url:\s*['"]?(https?:\/\/\S+?)['"]?\s*$/m);
      if (urlMatch) url = urlMatch[1];

      const titleMatch = yaml.match(/^title:\s*['"]?(.+?)['"]?\s*$/m);
      if (titleMatch) title = titleMatch[1].trim();

      const descMatch = yaml.match(/^description:\s*['"]?(.+?)['"]?\s*$/m);
      if (descMatch) description = descMatch[1].trim().replace(/^"(.*)"$/, "$1");
    }

    if (!url) {
      const mdUrl = body.match(/\*\*URL:\*\*\s*(https?:\/\/\S+)/);
      if (mdUrl) url = mdUrl[1];
    }
    if (!title) {
      const headingMatch = body.match(/^#\s+(.+)$/m);
      if (headingMatch) title = headingMatch[1].trim();
    }
    return { body, url, title, description };
  }

  // ── Unified Ingest Pipeline ──────────────────────────────────────
  //
  // All entry points (kb add, Web Clipper, url-new.txt, paste, orphan) call
  // this. 7 steps: preprocess → normalize → dup check → capture → LLM
  // processing → wiki page creation → tracking → post-processing.
  //
  // opts:
  //   - url:     source URL (optional if content provided)
  //   - content: pre-captured content (Web Clipper, paste)
  //   - title:   hint title
  //   - rawPath: path to existing raw file (Web Clipper case)
  //   - source:  "kb-add" | "web-clipper" | "url-new" | "paste" | "retry" | "orphan"
  //   - view:    optional view for status updates
  async ingestContent(opts) {
    const vaultPath = this.app.vault.adapter.basePath;
    const source = opts.source || "unknown";
    const view = opts.view || this._getActiveView();
    const updateStatus = (msg) => { if (view) view.updateStatus(msg); };

    // ── Step 0: PRE-PROCESS ──
    let contentBody = opts.content || "";
    let contentUrl = opts.url || "";
    let contentTitle = opts.title || "";
    if (contentBody) {
      const parsed = this._preprocessContent(contentBody);
      contentBody = parsed.body;
      if (!contentUrl && parsed.url) contentUrl = parsed.url;
      if (!contentTitle && parsed.title) contentTitle = parsed.title;
    }

    // ── Step 1: NORMALIZE ──
    let cleanUrl = contentUrl
      ? contentUrl.replace(/[?&](utm_\w+|s|t|rcm|ref|usp|si|igsh|fbclid)=[^&]*/g, "")
           .replace(/[?&]$/, "").replace(/\/+$/, "")
      : "";
    const isRepo = /github\.com\/[^/]+\/[^/]+/i.test(cleanUrl);
    const isTweet = /x\.com|twitter\.com/i.test(cleanUrl);
    const isPaper = /arxiv\.org|aclanthology\.org/i.test(cleanUrl);
    const isVideo = /youtube\.com|youtu\.be/i.test(cleanUrl);
    const rawSubdir = isRepo ? "raw/repos/artifacts" : isPaper ? "raw/papers/artifacts" : isVideo ? "raw/videos/artifacts" : "raw/webpages/artifacts";

    if (cleanUrl && (/\.(jpg|jpeg|png|gif|webp|svg|bmp)(\?|$)/i.test(cleanUrl) || /pbs\.twimg\.com\/media/i.test(cleanUrl))) {
      return { status: "failed", pageName: null, summary: "Image URL \u2014 use `kb add` with the page URL instead." };
    }

    // ── Step 2: DUPLICATE CHECK ──
    updateStatus("Checking for duplicates...");
    const dupResult = this.findDuplicate(cleanUrl || null, contentTitle || null, contentBody ? contentBody.split("\n") : null);
    if (dupResult) {
      if (contentBody) {
        let existingRawSize = 0;
        for (const d of ["wiki/format/webpages", "wiki/format/repos", "wiki/format/papers", "wiki/format/videos"]) {
          const dp = path.join(vaultPath, d, dupResult.page + ".md");
          if (fs.existsSync(dp)) {
            const head = fs.readFileSync(dp, "utf8").substring(0, 500);
            const rpMatch = head.match(/raw_path:\s*"?(\S+?)"?\s*$/m);
            if (rpMatch && fs.existsSync(path.join(vaultPath, rpMatch[1]))) {
              existingRawSize = fs.statSync(path.join(vaultPath, rpMatch[1])).size;
            }
            break;
          }
        }
        if (contentBody.length > existingRawSize * 1.1 && existingRawSize > 0) {
          console.log("[athena] ingest: content update", contentBody.length, "vs", existingRawSize);
          const existingRawPath = this._findRawPathForPage(dupResult.page);
          if (existingRawPath) {
            fs.writeFileSync(path.join(vaultPath, existingRawPath), contentBody);
          }
          const topicNames = this._getTopicNames();
          updateStatus("Re-summarizing with updated content...");
          const llmResult = await this.llmProcessContent(contentBody, cleanUrl, topicNames);
          const updateInput = {
            vault: vaultPath, raw_path: existingRawPath || rawSubdir + "/unknown.md",
            url: cleanUrl || null, source: source,
          };
          if (llmResult) updateInput.llm_result = llmResult;
          await this._runWikiPageBuilder(updateInput);
          updateStatus("Updating cross-references...");
          await this.runMechanical("lint", [], null, view);
          return { status: "updated", pageName: dupResult.page, summary: llmResult ? llmResult.summary : null };
        }
      }
      return { status: "duplicate", pageName: null, dupPage: dupResult.page, dupMethod: dupResult.method };
    }

    // ── Step 3: CAPTURE ──
    let rawContent = contentBody || "";
    let rawSlug = "";
    let rawFilePath = opts.rawPath || "";

    if (cleanUrl && !rawContent) {
      // Slug from canonical Python (single source of truth) — replaces the
      // previous local approximation that produced 'www-linkedin-com-...'
      // while Python writes 'linkedin-com-...'. Without this, the read-back
      // at line ~942 looks for a file kb-capture never wrote and silently
      // falls through to empty rawContent, producing sparse wiki pages.
      // Falls back to the local approximation if the subprocess fails
      // (Python missing, slug derivation rejects, etc.) so capture flow
      // still works in degraded environments.
      const _categoryFromSubdir = (sd) =>
        sd.startsWith("raw/repos") ? "repos" :
        sd.startsWith("raw/papers") ? "papers" :
        sd.startsWith("raw/videos") ? "videos" :
        "webpages";
      try {
        rawSlug = execFileSync(pythonCmd(), [
          "-c",
          "import sys; sys.path.insert(0, sys.argv[1]); " +
          "from slug import derive_slug; " +
          "print(derive_slug(sys.argv[2], sys.argv[3] or None, sys.argv[4] or None))",
          // Plugin-bundled bin/lib/ as of 1.0.9; falls back to
          // vault-side bin/lib/ for legacy / --full-vault dev layouts.
          path.dirname(resolvePythonScript(this, "bin/lib/slug.py")),
          _categoryFromSubdir(rawSubdir),
          cleanUrl,
          contentTitle || "",
        ], { encoding: "utf8", timeout: 5000, stdio: ["ignore", "pipe", "pipe"] }).trim();
      } catch (e) {
        console.warn("[athena] canonical slug derivation failed, falling back:", e.message);
        rawSlug = cleanUrl.replace(/https?:\/\//, "").replace(/[^a-z0-9]/gi, "-").replace(/-{2,}/g, "-").substring(0, 60);
      }
      rawFilePath = path.join(vaultPath, rawSubdir, rawSlug + ".md");

      try {
        if (fs.existsSync(rawFilePath) && fs.statSync(rawFilePath).size < 600) {
          fs.unlinkSync(rawFilePath);
        }
      } catch {}

      updateStatus("Capturing URL...");
      const browserText = await this.browserCapture(cleanUrl, updateStatus);
      if (browserText) {
        console.log("[athena] ingest: BrowserWindow captured", browserText.length, "chars");
        const repoMatch = cleanUrl.match(/github\.com\/([^/]+)\/([^/?#]+)/i);
        let rawTitle = isRepo && repoMatch ? `Git \u2014 ${repoMatch[2]}` : isTweet ? "X Post" : "Page";
        // YAML frontmatter is REQUIRED so create_wiki_page → preprocess_content
        // can extract the source URL when later turning this raw into a wiki
        // page. The previous "URL intentionally omitted" version produced wiki
        // pages with no `url:` field and therefore no Source link in the body
        // (the user-reported missing-source bug fixed in 0.9.10).
        const _rawTitleEsc = rawTitle.replace(/\\/g, "\\\\").replace(/"/g, '\\"');
        const _urlEsc = cleanUrl.replace(/\\/g, "\\\\").replace(/"/g, '\\"');
        rawContent = `---\ntitle: "${_rawTitleEsc}"\nsource: "${_urlEsc}"\ncaptured_at: "${new Date().toISOString()}"\nclipped_via: "browser-capture"\n---\n\n# ${rawTitle}\n\n${browserText}\n`;
        // Ensure raw/<type>/artifacts/ exists — Community-Plugins / fresh
        // vault installs lack the three-layer dir structure (the Python
        // backend creates it). Without this, writeFileSync throws ENOENT
        // even though browser capture succeeded — opaque user-facing error.
        try { fs.mkdirSync(path.dirname(rawFilePath), { recursive: true }); } catch {}
        fs.writeFileSync(rawFilePath, rawContent);
      } else {
        updateStatus("Webview capture failed, trying Python backend...");
        const captureResult = await this.runMechanical("add", [cleanUrl], null, view);
        const captureOutput = (captureResult.stdout || "") + (captureResult.stderr || "");
        if (captureOutput.includes("Saved:") || captureOutput.includes("already exists")) {
          try { rawContent = fs.readFileSync(rawFilePath, "utf8"); } catch {
            for (const d of ["raw/webpages/artifacts", "raw/repos/artifacts", "raw/papers/artifacts", "raw/videos/artifacts"]) {
              try { rawContent = fs.readFileSync(path.join(vaultPath, d, rawSlug + ".md"), "utf8"); rawFilePath = path.join(vaultPath, d, rawSlug + ".md"); break; } catch {}
            }
          }
        } else if ((captureResult.stdout || "").includes("THIN_CONTENT")) {
          return { status: "failed", pageName: null, summary: "Could not capture full content (requires authentication)." };
        } else {
          // All three capture paths exhausted: BrowserWindow + webview
          // returned <100 chars or threw, and the bin/kb shell fallback
          // either reported an error or isn't present (Community-Plugin
          // installs don't ship the Python backend). Common root causes:
          // page blocks automation (Cloudflare et al.), Linux sandbox
          // restrictions on the BrowserWindow path, or missing Python
          // backend in the vault. Web Clipper sidesteps all three.
          return {
            status: "failed",
            pageName: null,
            summary:
              "Browser capture failed (this can happen on Linux with sandbox restrictions, " +
              "or with pages that block automation like Cloudflare-protected sites). " +
              "Try the Obsidian Web Clipper extension instead.",
          };
        }
      }
    } else if (rawFilePath && !rawContent) {
      let fileContent = "";
      try { fileContent = fs.readFileSync(path.join(vaultPath, rawFilePath), "utf8"); } catch {
        try { fileContent = fs.readFileSync(rawFilePath, "utf8"); } catch {}
      }
      if (fileContent) {
        const parsed = this._preprocessContent(fileContent);
        rawContent = parsed.body;
        if (!cleanUrl && parsed.url) {
          cleanUrl = parsed.url.replace(/[?&](utm_\w+|s|t|rcm|ref|usp|si|igsh|fbclid)=[^&]*/g, "")
            .replace(/[?&]$/, "").replace(/\/+$/, "");
        }
      }
      rawSlug = path.basename(rawFilePath, ".md");
    } else if (rawContent && !rawFilePath) {
      rawSlug = (contentTitle || "paste-" + Date.now()).toLowerCase().replace(/[^a-z0-9]/gi, "-").replace(/-{2,}/g, "-").substring(0, 60);
      rawFilePath = path.join(vaultPath, rawSubdir, rawSlug + ".md");
      // Wrap with YAML frontmatter so create_wiki_page can extract source URL
      // (same rationale as the browserCapture branch above). Skip wrapping if
      // the user-pasted content already has its own `---` frontmatter — avoid
      // double-wrapping (was the lint #48 bug class on the writer side).
      if (!rawContent.startsWith("---")) {
        const _titleEsc = (contentTitle || rawSlug).replace(/\\/g, "\\\\").replace(/"/g, '\\"');
        const _urlLine = cleanUrl ? `source: "${cleanUrl.replace(/\\/g, "\\\\").replace(/"/g, '\\"')}"\n` : "";
        rawContent = `---\ntitle: "${_titleEsc}"\n${_urlLine}captured_at: "${new Date().toISOString()}"\nclipped_via: "paste"\n---\n\n${rawContent}\n`;
      }
      // Ensure raw/<type>/ exists for fresh-vault installs (see line 1001 fix).
      try { fs.mkdirSync(path.dirname(rawFilePath), { recursive: true }); } catch {}
      fs.writeFileSync(rawFilePath, rawContent);
    }

    if (!rawContent) {
      return { status: "failed", pageName: null, summary: "No content captured." };
    }

    // Post-capture dup check (title + content fingerprint)
    const titleMatch = rawContent.match(/^#\s+(.+)/m) || rawContent.match(/^title:\s*['"]?(.+?)['"]?\s*$/m);
    const rawTitle = titleMatch ? titleMatch[1].trim() : null;
    const postCaptureDup = this.findDuplicate(null, rawTitle, rawContent.split("\n"));
    if (postCaptureDup) {
      console.log("[athena] ingest: post-capture duplicate", postCaptureDup);
      return { status: "duplicate", pageName: null, dupPage: postCaptureDup.page, dupMethod: postCaptureDup.method };
    }

    // ── Step 4: LLM PROCESSING ──
    const topicNames = this._getTopicNames();
    updateStatus("Reading and summarizing...");
    const llmResult = await this.llmProcessContent(rawContent, cleanUrl || rawSlug, topicNames);

    // ── Step 5: WIKI PAGE CREATION ──
    updateStatus("Creating wiki page...");
    const relRawPath = rawFilePath.startsWith(vaultPath)
      ? rawFilePath.substring(vaultPath.length + 1)
      : (rawSubdir + "/" + (rawSlug || path.basename(rawFilePath, ".md")) + ".md");
    const wikiInput = {
      vault: vaultPath,
      raw_path: relRawPath,
      url: cleanUrl || null,
      title: contentTitle || null,
      source: source,
    };
    if (llmResult) {
      console.log("[athena] ingest: LLM result", { title: llmResult.title, tags: llmResult.tags?.length, related: llmResult.related?.length });
      wikiInput.llm_result = llmResult;
    } else {
      console.log("[athena] ingest: no LLM, Python fallback will apply naming conventions");
    }
    const wikiResult = await this._runWikiPageBuilder(wikiInput);
    const pageName = wikiResult ? wikiResult.page_name : null;
    if (wikiResult) {
      console.log("[athena] ingest: wiki result", { status: wikiResult.status, page: wikiResult.page_name });
    }

    // ── Step 6: TRACKING (fallback if Python missed it) ──
    if (cleanUrl && !wikiResult) {
      const tsvPath = path.join(vaultPath, "inbox", "url-resolved.tsv");
      try {
        let tsv = fs.readFileSync(tsvPath, "utf8");
        const urlLower = cleanUrl.toLowerCase();
        tsv = tsv.split("\n").filter(l => !l.toLowerCase().includes(urlLower)).join("\n");
        const ts = new Date().toISOString();
        tsv += `\ncaptured\t${pageName || rawSlug}\t${cleanUrl}\t${ts}\n`;
        tsv = tsv.replace(/\n{3,}/g, "\n");
        fs.writeFileSync(tsvPath, tsv);
      } catch (e) { console.log("[athena] ingest: url-resolved update failed:", e.message); }
    }

    // ── Step 7: POST-PROCESSING ──
    updateStatus("Updating cross-references...");
    await this.runMechanical("lint", [], null, view);

    return { status: "created", pageName, summary: llmResult ? llmResult.summary : null };
  }

  /** Call the shared Python wiki page builder (bin/lib/wiki_page.py). */
  _runWikiPageBuilder(input) {
    const vaultPath = this.app.vault.adapter.basePath;
    // 1.0.9+: prefer the plugin-bundled wiki_page.py over the
    // vault-side copy. End users no longer need a cloned Athena vault
    // — just a Python install + `pip install pydantic`.
    const scriptPath = resolvePythonScript(this, "bin/lib/wiki_page.py");
    return new Promise((resolve) => {
      const proc = spawn(pythonCmd(), [scriptPath, "--stdin"], {
        cwd: vaultPath,
        env: { ...process.env, PATH: buildEnhancedPath() },
        stdio: ["pipe", "pipe", "pipe"],
      });
      let stdout = "", stderr = "";
      proc.stdout.on("data", (d) => { stdout += d.toString(); });
      proc.stderr.on("data", (d) => { stderr += d.toString(); });
      proc.stdin.write(JSON.stringify(input));
      proc.stdin.end();
      const timer = setTimeout(() => {
        try { proc.kill("SIGTERM"); } catch {}
        console.log("[athena] wiki page builder timed out");
        resolve(null);
      }, 15000);
      proc.on("close", (code) => {
        clearTimeout(timer);
        // Bumped 200 → 2000 chars in 1.0.10 — Python tracebacks regularly
        // exceed 200 chars and the 200-char cap routinely truncated before
        // the actual "<ErrorType>: message" tail, leaving the user with a
        // file/line pointer and no error class. 2000 covers any reasonable
        // single-frame traceback; multi-frame traces may still need the
        // manual reproduction command in CHANGELOG to see fully.
        if (stderr) console.log("[athena] wiki_page.py stderr:", stderr.substring(0, 2000));
        try {
          const result = JSON.parse(stdout.trim());
          resolve(result);
        } catch (e) {
          console.log("[athena] wiki_page.py parse error:", e.message, "raw:", stdout.substring(0, 200));
          resolve(null);
        }
      });
      proc.on("error", (err) => {
        clearTimeout(timer);
        console.log("[athena] wiki_page.py spawn error:", err.message);
        resolve(null);
      });
    });
  }

  _getTopicNames() {
    const topicNames = [];
    try {
      const topicDir = path.join(this.app.vault.adapter.basePath, "wiki", "topics");
      if (fs.existsSync(topicDir)) {
        for (const f of fs.readdirSync(topicDir)) {
          if (f.endsWith(".md") && !f.startsWith("_")) topicNames.push(f.replace(/\.md$/, ""));
        }
      }
    } catch {}
    return topicNames;
  }

  _findRawPathForPage(pageName) {
    const vaultPath = this.app.vault.adapter.basePath;
    for (const d of ["wiki/format/webpages", "wiki/format/repos", "wiki/format/papers", "wiki/format/videos", "wiki/format/images"]) {
      const fp = path.join(vaultPath, d, pageName + ".md");
      if (fs.existsSync(fp)) {
        const head = fs.readFileSync(fp, "utf8").substring(0, 500);
        const match = head.match(/raw_path:\s*"?(\S+?)"?\s*$/m);
        return match ? match[1] : null;
      }
    }
    return null;
  }

  // ── Duplicate detection (URL + title + content fingerprint) ──────

  findDuplicate(url, title, bodyLines) {
    const vaultPath = this.app.vault.adapter.basePath;
    const wikiDirs = ["wiki/format/webpages", "wiki/format/repos", "wiki/format/papers", "wiki/format/videos", "wiki/format/images"];

    const cleanUrl = url ? url.replace(/[?&](utm_\w+|s|t|rcm|ref|usp)=[^&]*/g, "").replace(/[?&]$/, "").replace(/\/+$/, "").toLowerCase() : "";
    const normTitle = title ? title.toLowerCase().replace(/[^a-z0-9\s]/g, "").replace(/\s+/g, " ").trim() : "";
    const fingerprint = [];
    if (bodyLines && bodyLines.length > 0) {
      const fullText = bodyLines.join("\n");
      const paragraphs = fullText.split(/\n\s*\n/);
      for (const p of paragraphs) {
        const cleaned = p.replace(/\s+/g, " ").trim().toLowerCase();
        if (cleaned.length < 50) continue;
        if (/^\d|^http|^published|^created|^date|^updated|^tags|^source|^author|^views|^likes|^\*\*url/i.test(cleaned)) continue;
        fingerprint.push(cleaned);
        if (fingerprint.length >= 3) break;
      }
    }

    try {
      for (const dir of wikiDirs) {
        const fullDir = path.join(vaultPath, dir);
        if (!fs.existsSync(fullDir)) continue;
        for (const file of fs.readdirSync(fullDir)) {
          if (!file.endsWith(".md") || file.startsWith("_")) continue;
          const filePath = path.join(fullDir, file);
          const content = fs.readFileSync(filePath, "utf8");
          const pageName = file.replace(/\.md$/, "");
          const contentLower = content.substring(0, 600).toLowerCase();

          if (cleanUrl && (contentLower.includes(cleanUrl) || contentLower.includes(url.toLowerCase()))) {
            return { page: pageName, method: "URL" };
          }

          if (normTitle && normTitle.length > 10) {
            const titleMatch = content.match(/^title:\s*"?(.+?)"?\s*$/m);
            if (titleMatch) {
              const pageTitle = titleMatch[1].toLowerCase().replace(/[^a-z0-9\s]/g, "").replace(/\s+/g, " ").trim();
              if (pageTitle === normTitle || (pageTitle.length > 15 && normTitle.includes(pageTitle)) || (normTitle.length > 15 && pageTitle.includes(normTitle))) {
                return { page: pageName, method: "title" };
              }
              const stopWords = new Set(["the", "a", "an", "of", "for", "and", "in", "on", "to", "with", "is", "at"]);
              const wordsA = normTitle.split(" ").filter(w => w.length > 2 && !stopWords.has(w));
              const wordsB = pageTitle.split(" ").filter(w => w.length > 2 && !stopWords.has(w));
              if (wordsA.length >= 3 && wordsB.length >= 3) {
                const setA = new Set(wordsA);
                const overlap = wordsB.filter(w => setA.has(w)).length;
                const ratio = overlap / Math.min(wordsA.length, wordsB.length);
                if (ratio >= 0.6) {
                  return { page: pageName, method: "title (similar)" };
                }
              }
            }
          }

          if (fingerprint.length >= 2) {
            const bodyStart = content.indexOf("\n---", 3);
            if (bodyStart > 0) {
              const pageBody = content.substring(bodyStart + 4);
              const pageParagraphs = pageBody.split(/\n\s*\n/)
                .map(p => p.replace(/\s+/g, " ").trim().toLowerCase())
                .filter(p => p.length >= 50)
                .slice(0, 5);
              let matches = 0;
              for (const fp of fingerprint) {
                for (const pp of pageParagraphs) {
                  const shorter = fp.length < pp.length ? fp : pp;
                  const longer = fp.length < pp.length ? pp : fp;
                  if (longer.includes(shorter) || shorter.includes(longer.substring(0, shorter.length))) {
                    matches++;
                    break;
                  }
                }
              }
              if (matches >= 2) {
                return { page: pageName, method: "content" };
              }
            }
          }
        }
      }
    } catch (e) {
      console.log("[athena] dup check error:", e.message);
    }
    return null;
  }

  // ── LLM content processing (haiku/sonnet one-shot, JSON response) ──

  async llmProcessContent(rawContent, url, topicNames) {
    const claudePath = this.settings.claudePath || findClaudeBinary();
    if (!claudePath) return null;

    const isTweet = /x\.com|twitter\.com/i.test(url);
    const isRepo = /github\.com\/[^/]+\/[^/]+/i.test(url);
    const sourceHint = isTweet ? "tweet/social media post" : isRepo ? "GitHub repository" : "webpage";

    const content = rawContent.substring(0, 4000);
    const topicList = topicNames.slice(0, 30).join(", ");

    let namingRules = "";
    let taggingRules = "";
    try {
      const rulesPath = path.join(this.app.vault.adapter.basePath, "RULES.md");
      const rulesContent = fs.readFileSync(rulesPath, "utf8");
      const namingMatch = rulesContent.match(/## Naming Convention\n[\s\S]*?\n([\s\S]*?)(?=\n## |\n---|\n$)/);
      if (namingMatch) namingRules = namingMatch[1].trim();
      const taggingMatch = rulesContent.match(/## Tagging Rules\n[\s\S]*?\n([\s\S]*?)(?=\n## |\n---|\n$)/);
      if (taggingMatch) taggingRules = taggingMatch[1].trim();
    } catch {}
    if (!namingRules) {
      namingRules = `- Twitter/X posts: "X \u2014 <topic description>" (never include @username)
- GitHub repos: "Git \u2014 <repo-name> \u2014 <short description>" (never include owner/org)
- LinkedIn: plain topic title (no username, no platform prefix)
- Other: descriptive topic title
- Max 65 characters`;
    }

    const prompt = `You are a knowledge base assistant. Given this captured ${sourceHint}, return ONLY a JSON object (no markdown, no explanation).

Source URL: ${url}

Raw content:
${content}

Existing topic pages in the knowledge base: ${topicList}

Return this exact JSON structure:
{
  "title": "descriptive title following the naming convention below",
  "summary": "2-3 sentence summary of the key insight or content",
  "tags": ["tag1", "tag2"],
  "related": ["Exact Topic Page Name", "Another Topic"],
  "body": "cleaned markdown content \u2014 remove UI artifacts (navigation, follower counts, 'See new posts', etc), keep the substance"
}

Rules:
- title: Follow this EXACT naming convention:
${namingRules}
- title: no colons, no special filename chars (*?"<>|)
- title: use em dash (\u2014) not hyphen (-) in page titles
${taggingRules ? `- tags: follow these tagging rules:\n${taggingRules}` : "- tags: pick from [ai-agents, claude-code, llm, ml, security, deep-learning, memory, obsidian, python, rag, tools, skills, course, paper, repo, webpage, video]"}
- related: only include topic names from the list above that are genuinely related
- body: max 3000 chars, clean markdown, no HTML, no UI junk
- summary: 2-3 sentences, not one-liners. Focus on the key insight, not generic description`;

    return new Promise((resolve) => {
      const model = this.settings.model || "sonnet";
      // Windows .cmd shim handling: claudePath is typically claude.cmd
      // on Windows (npm install path). Node 20+ refuses to spawn .cmd
      // bare — returns EINVAL. wrapForCmdShim wraps with cmd.exe + the
      // windowsVerbatimArguments flag and handles arg quoting safely.
      // No-op on POSIX (isWindowsShim returns false).
      let spawnBin = claudePath;
      let spawnArgs = ["-p", prompt, "--model", model, "--max-turns", "1"];
      let extraSpawnOpts = {};
      if (isWindowsShim(claudePath)) {
        const wrapped = wrapForCmdShim(claudePath, spawnArgs);
        spawnBin = wrapped.command;
        spawnArgs = wrapped.args;
        extraSpawnOpts = wrapped.options || {};
      }
      const proc = spawn(spawnBin, spawnArgs, {
        cwd: this.app.vault.adapter.basePath,
        env: { ...process.env, PATH: buildEnhancedPath() },
        stdio: ["pipe", "pipe", "pipe"],
        ...extraSpawnOpts,
      });

      let stdout = "", stderr = "";
      const timer = setTimeout(() => {
        try { proc.kill("SIGTERM"); } catch {}
        console.log("[athena] LLM process timed out");
        resolve(null);
      }, 30000);

      proc.stdout.on("data", (d) => { stdout += d.toString(); });
      proc.stderr.on("data", (d) => { stderr += d.toString(); });
      proc.on("close", (code) => {
        clearTimeout(timer);
        console.log("[athena] LLM done", { code, len: stdout.length });
        // Surface stderr whenever claude.cmd exits non-zero. Without this,
        // a code:255 (auth failure, missing API key, model error, etc.)
        // is invisible — the user only sees "LLM done {code: 255, len: 0}"
        // followed by "LLM parse error: Unexpected end of JSON input" and
        // has no diagnostic to act on.
        if (code !== 0 && stderr) {
          console.log("[athena] LLM stderr (exit code " + code + "):",
            stderr.substring(0, 2000));
        }
        try {
          let json = stdout.trim();
          const jsonMatch = json.match(/\{[\s\S]*\}/);
          if (jsonMatch) json = jsonMatch[0];
          const parsed = JSON.parse(json);
          if (parsed.title && parsed.body) {
            resolve(parsed);
          } else {
            console.log("[athena] LLM returned incomplete JSON:", json.substring(0, 200));
            resolve(null);
          }
        } catch (e) {
          console.log("[athena] LLM parse error:", e.message, "raw:", stdout.substring(0, 300));
          resolve(null);
        }
      });
    });
  }

  // ── Browser-based capture (Electron's Chromium) ──────────────────

  abortBrowserCapture() {
    if (this._browserCaptureCleanup) {
      this._browserCaptureCleanup();
      this._browserCaptureCleanup = null;
    }
  }

  _probeElectronApis() {
    if (this._electronProbed) return;
    this._electronProbed = true;
    try {
      const electron = require("electron");
      console.log("[athena] electron module available:", Object.keys(electron).join(", "));
      if (electron.remote) {
        console.log("[athena] electron.remote available:", Object.keys(electron.remote).join(", "));
      }
      if (electron.ipcRenderer) {
        console.log("[athena] ipcRenderer available");
      }
      const BW = (electron.remote && electron.remote.BrowserWindow) ||
                 (electron.BrowserWindow);
      console.log("[athena] BrowserWindow:", BW ? "available" : "not available");
      this._electronBrowserWindow = BW || null;
      this._electron = electron;
    } catch (e) {
      console.log("[athena] electron module not available:", e.message);
      this._electronBrowserWindow = null;
      this._electron = null;
    }
    try {
      const remote = require("@electron/remote");
      console.log("[athena] @electron/remote available:", Object.keys(remote).join(", "));
      if (remote.BrowserWindow) {
        this._electronBrowserWindow = remote.BrowserWindow;
        console.log("[athena] BrowserWindow from @electron/remote: available");
      }
    } catch (e) {
      console.log("[athena] @electron/remote not available:", e.message);
    }
  }

  // updateStatus (optional) surfaces fallback transitions in the chat
  // status line. Without it the user sees "Capturing URL..." for the
  // full duration of all retries (up to 30s) with no feedback. With it
  // they see "Browser capture failed, trying webview..." etc. and can
  // ctrl+C or wait knowingly.
  async browserCapture(url, updateStatus = null) {
    this._probeElectronApis();

    if (this._electronBrowserWindow) {
      console.log("[athena] trying BrowserWindow capture for:", url);
      const text = await this._browserWindowCapture(url);
      if (text) return text;
      if (updateStatus) updateStatus("Browser capture failed, trying webview...");
    }

    console.log("[athena] trying webview capture for:", url);
    return this._webviewCapture(url);
  }

  async _browserWindowCapture(url) {
    const BW = this._electronBrowserWindow;
    if (!BW) return null;
    let win = null;
    try {
      win = new BW({
        width: 1280,
        height: 900,
        show: false,
        webPreferences: {
          nodeIntegration: false,
          contextIsolation: true,
          // Sandbox stays on — Athena's CLAUDE.md requires it for browser
          // capture. On Linux this can cause the renderer to hang if the
          // chrome-sandbox helper is misconfigured (AppArmor + Snap
          // Obsidian is the common case); the outer 15s timeout below is
          // the fail-safe so the user falls through to webview/shell
          // instead of staring at "Capturing URL..." forever.
          sandbox: true,
        },
      });
      // Wrap the full load+extract chain in a 15s race. A hung loadURL
      // (the Linux-sandbox failure mode) leaves the entire await chain
      // unfired — including the 6s setTimeout that's nested inside it —
      // so without this race the browserCapture path never falls
      // through to _webviewCapture. Matches _webviewCapture's existing
      // 15s budget for symmetry.
      const text = await Promise.race([
        (async () => {
          await win.loadURL(url);
          await new Promise(r => setTimeout(r, 6000));
          return await win.webContents.executeJavaScript(`
            (function() {
              var tweets = document.querySelectorAll('[data-testid="tweetText"]');
              if (tweets.length > 0) {
                return Array.from(tweets).map(function(el) { return el.innerText; }).join("\\n\\n");
              }
              var article = document.querySelector('article, main, [role="main"]');
              if (article) return article.innerText;
              return document.body.innerText || "";
            })()
          `);
        })(),
        new Promise((_, reject) =>
          setTimeout(() => reject(new Error("BrowserWindow timeout after 15s")), 15000)
        ),
      ]);
      try { win.close(); } catch {}
      console.log("[athena] BrowserWindow extracted:", text ? text.length + " chars" : "empty");
      return text && text.trim().length > 100 ? text.trim() : null;
    } catch (e) {
      console.log("[athena] BrowserWindow capture failed:", e.message);
      try { if (win && !win.isDestroyed()) win.close(); } catch {}
      return null;
    }
  }

  async _webviewCapture(url) {
    return new Promise((resolve) => {
      let resolved = false;

      try {
        const testWv = document.createElement("webview");
        if (!testWv.executeJavaScript) {
          console.log("[athena] webview not supported \u2014 no executeJavaScript");
          resolve(null);
          return;
        }
        console.log("[athena] webview tag supported");
      } catch (e) {
        console.log("[athena] webview not available:", e.message);
        resolve(null);
        return;
      }

      const webview = document.createElement("webview");
      webview.setAttribute("src", url);
      webview.setAttribute("useragent", "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36");
      webview.style.width = "1280px";
      webview.style.height = "900px";
      webview.style.position = "absolute";
      webview.style.left = "-9999px";
      document.body.appendChild(webview);

      const cleanup = () => {
        if (!resolved) resolved = true;
        try { document.body.removeChild(webview); } catch {}
      };

      this._browserCaptureCleanup = () => { cleanup(); resolve(null); };

      const timeout = setTimeout(() => {
        console.log("[athena] webview timeout \u2014 giving up after 15s");
        if (!resolved) { resolved = true; cleanup(); resolve(null); }
      }, 15000);

      webview.addEventListener("dom-ready", () => {
        console.log("[athena] webview dom-ready, waiting 6s for JS rendering...");
        setTimeout(async () => {
          if (resolved) return;
          try {
            const text = await webview.executeJavaScript(`
              (function() {
                var tweets = document.querySelectorAll('[data-testid="tweetText"]');
                if (tweets.length > 0) {
                  return Array.from(tweets).map(function(el) { return el.innerText; }).join("\\n\\n");
                }
                var article = document.querySelector('article, main, [role="main"]');
                if (article) return article.innerText;
                return document.body.innerText || "";
              })()
            `);
            clearTimeout(timeout);
            cleanup();
            resolved = true;
            console.log("[athena] webview extracted:", text ? text.length + " chars" : "empty");
            resolve(text && text.trim().length > 100 ? text.trim() : null);
          } catch (e) {
            console.log("[athena] webview extract failed:", e.message);
            clearTimeout(timeout);
            cleanup();
            resolved = true;
            resolve(null);
          }
        }, 6000);
      });

      webview.addEventListener("did-fail-load", (event) => {
        console.log("[athena] webview did-fail-load:", event.errorCode, event.errorDescription);
        if (!resolved) { resolved = true; clearTimeout(timeout); cleanup(); resolve(null); }
      });

      webview.addEventListener("console-message", (event) => {
        console.log("[athena] webview console:", event.message);
      });
    });
  }

  // ── File watchers (clip dirs + url-new.txt) ──────────────────────

  /**
   * Parse settings.clippingsFolder into absolute directory paths.
   * Comma-separated, deduped, leading-slash-stripped, empty-item-filtered.
   */
  _resolveClipDirs() {
    const vaultPath = this.app.vault.adapter.basePath;
    const raw = this.settings.clippingsFolder || "clippings, inbox/Clippings";
    const paths = raw.split(",")
      .map((p) => p.trim().replace(/^\/+/, ""))
      .filter(Boolean);
    const seen = new Set();
    const out = [];
    for (const p of paths) {
      const abs = path.join(vaultPath, p);
      if (!seen.has(abs)) { seen.add(abs); out.push(abs); }
    }
    return out;
  }

  _setupClipWatcher(clipDir) {
    try {
      const watcher = fs.watch(clipDir, (eventType, filename) => {
        const skipFiles = ["URL Tracker.md", "Add URLs.md", "url-new.txt"];
        if (eventType === "rename" && filename && filename.endsWith(".md") && !filename.startsWith(".")
            && !skipFiles.includes(filename)) {
          const filePath = path.join(clipDir, filename);
          if (!this._processedClips) this._processedClips = new Set();
          // Key by absolute path — same filename can exist in multiple
          // watched dirs and we don't want one to block the other.
          if (this._processedClips.has(filePath)) return;
          this._processedClips.add(filePath);
          setTimeout(async () => {
            try {
              if (!fs.existsSync(filePath)) return;
              console.log("[athena] New clip detected:", filePath);
              await this._handleClipFile(clipDir, filename, filePath);
            } catch (e) {
              console.error("[athena] _setupClipWatcher clip handler error:", e);
              this._processedClips.delete(filePath);  // allow watchdog retry
            }
          }, 2000);
        }
      });
      this._clipWatchers.push({ dir: clipDir, watcher });
    } catch (e) {
      console.log("[athena] Could not watch", clipDir, ":", e.message);
    }
  }

  /** Shared clip processing for both initial watcher and watchdog retry. */
  async _handleClipFile(clipDir, filename, filePath) {
    try {
      const view = this._getActiveView();
      if (!view) {
        new Notice(`Athena: Clip saved \u2014 open Athena to process.`);
        return;
      }
      if (view.isStreaming) return;

      let clipUrl = "", clipContent = "";
      try {
        clipContent = fs.readFileSync(filePath, "utf8");
        const urlMatch = clipContent.match(/source:\s*['"]?(https?:\/\/\S+?)['"]?\s*$/m) ||
                         clipContent.match(/url:\s*['"]?(https?:\/\/\S+?)['"]?\s*$/m);
        if (urlMatch) clipUrl = urlMatch[1];
      } catch {}
      const cleanFilename = filename.replace(/^\(\d+\)\s*/, "");
      const clipName = cleanFilename.replace(".md", "");
      const clipTitleMatch = clipContent.match(/^title:\s*['"]?(.+?)['"]?\s*$/m) || clipContent.match(/^#\s+(.+)/m);
      const clipTitle = clipTitleMatch ? clipTitleMatch[1].trim() : clipName;

      // Route through bin/lib/process_clip.py — the canonical writer that
      // applies URL-derived slug (slug.derive_slug), per-host URL canonicalization
      // (url_canonical.canonicalize), and schema validation (raw_writer).
      // Replaces the title-derived slug bypass that produced collision-bait
      // raws like raw/webpages/artifacts/post-linkedin.md whenever LinkedIn
      // served the generic "Post | LinkedIn" title (every LinkedIn post does).
      // Subprocess adds ~150ms latency but eliminates drift between JS and
      // Python slug derivation forever.
      const vaultBase = this.app.vault.adapter.basePath;
      let rawPath, fullRawPath;
      try {
        const out = execFileSync(pythonCmd(), [
          // Plugin-bundled (1.0.9+) → vault-side fallback.
          resolvePythonScript(this, "bin/lib/process_clip.py"),
          vaultBase,
          filePath,
        ], { encoding: "utf8", timeout: 30000, stdio: ["ignore", "pipe", "pipe"] }).trim();
        fullRawPath = out;
        rawPath = path.relative(vaultBase, fullRawPath);
      } catch (e) {
        const stderr = (e.stderr && e.stderr.toString()) || e.message || String(e);
        console.error("[athena] process_clip failed for clip:", filename, stderr);
        new Notice(`Athena: Clip processing failed — ${stderr.split("\n")[0]}`);
        if (this._processedClips) this._processedClips.delete(filePath);
        return;
      }
      const processedDir = path.join(clipDir, ".processed");
      try { fs.mkdirSync(processedDir, { recursive: true }); } catch {}
      try { fs.renameSync(filePath, path.join(processedDir, filename)); } catch {}

      view.addSystemMessage(`New clip received: ${clipName}`);
      view.startStreamingMessage();
      view.isStreaming = true;
      // Per Gryphon issue #3: input stays enabled while clip ingest runs.
      try {
        const result = await this.ingestContent({
          url: clipUrl || null,
          content: clipContent,
          title: clipTitle,
          rawPath: rawPath,
          source: "web-clipper",
          view,
        });
        if (result.status === "duplicate") {
          view.finalizeStreamingMessage(
            `**Already in knowledge base:** [[${result.dupPage}]]\n\nMatched by ${result.dupMethod}.`,
            `Already captured: ${result.dupPage}`
          );
        } else if (result.status === "updated") {
          view.finalizeStreamingMessage(
            `**Updated:** [[${result.pageName}]]\n\nRefreshed with better content from clip.` +
            (result.summary ? `\n\n${result.summary}` : ""),
            `Updated: ${result.pageName}`
          );
        } else if (result.status === "created") {
          const lines = [];
          if (result.pageName) {
            lines.push(`**Captured:** ${clipUrl || clipName}`);
            lines.push(`**Page created:** [[${result.pageName}]]`);
            if (result.summary) lines.push(`\n${result.summary}`);
          } else {
            lines.push(`**Clip saved:** ${clipUrl || clipName}`);
          }
          view.finalizeStreamingMessage(lines.join("\n"), result.pageName ? `Page created: ${result.pageName}` : "Clip saved");
        } else {
          view.finalizeStreamingMessage(result.summary || "Clip processing failed.", "Failed");
        }
      } finally {
        view.isStreaming = false;
        // Per Gryphon issue #3: don't re-toggle disabled. Drain queued
        // prompts that arrived during the clip ingest.
        if (typeof view._drainQueuedPrompts === "function") view._drainQueuedPrompts();
      }
    } catch (e) {
      console.error("[athena] _handleClipFile error:", e);
      if (this._processedClips) this._processedClips.delete(filePath);  // allow watchdog retry
    }
  }

  _setupUrlNewWatcher() {
    const urlNewPath = path.join(this.app.vault.adapter.basePath, "inbox", "url-new.txt");
    // fs.watch throws ENOENT if the file doesn't exist — on a fresh
    // vault install (no Python backend) it never has. Create as empty
    // so the watcher attaches and future URL drops trigger ingest.
    // Parent inbox/ is ensured in onload() before this runs.
    try {
      if (!fs.existsSync(urlNewPath)) fs.writeFileSync(urlNewPath, "");
    } catch (e) {
      console.log("[athena] Could not create url-new.txt:", e.message);
      return;
    }
    try {
      this._urlNewWatcher = fs.watch(urlNewPath, (eventType) => {
        if (eventType !== "change") return;
        this._processUrlNewDebounced();
      });
    } catch (e) {
      console.log("[athena] Could not watch url-new.txt:", e.message);
    }
  }

  _processUrlNewDebounced() {
    if (this._urlNewTimer) clearTimeout(this._urlNewTimer);
    this._urlNewTimer = setTimeout(() => this._processUrlNew(), 3000);
  }

  async _processUrlNew() {
    const vaultPath = this.app.vault.adapter.basePath;
    const urlNewPath = path.join(vaultPath, "inbox", "url-new.txt");
    try {
      const content = fs.readFileSync(urlNewPath, "utf8").trim();
      if (!content) return;
      const urls = content.split("\n").map(l => l.trim()).filter(l => l.startsWith("http"));
      if (urls.length === 0) return;
      console.log("[athena] processing url-new.txt:", urls.length, "URLs");

      const view = this._getActiveView();
      if (view) {
        if (view.isStreaming) return;

        view.addSystemMessage(`New URL${urls.length > 1 ? "s" : ""} detected in inbox (${urls.length})`);
        view.startStreamingMessage();
        view.isStreaming = true;
        // Per Gryphon issue #3: input stays enabled while URLs ingest.
        let created = 0;
        const skippedDups = [];

        try {
          for (const url of urls) {
            view.updateStatus(`Processing ${url.substring(0, 50)}...`);
            const result = await this.ingestContent({ url, source: "url-new", view });
            if (result.status === "created" || result.status === "updated") {
              created++;
            } else if (result.status === "duplicate") {
              skippedDups.push({ url, page: result.dupPage });
            }
          }

          const lines = [];
          if (created > 0) lines.push(`**${created} page${created > 1 ? "s" : ""} created**`);
          if (skippedDups.length > 0) {
            lines.push("");
            lines.push(`**${skippedDups.length} already in knowledge base:**`);
            for (const d of skippedDups) lines.push(`- [[${d.page}]]`);
          }
          view.finalizeStreamingMessage(
            lines.join("\n") || "Nothing to process.",
            created > 0 ? `${created} page${created > 1 ? "s" : ""} created` : "Nothing to process"
          );
        } finally {
          view.isStreaming = false;
          // Per Gryphon issue #3: don't re-toggle disabled. Drain queued
          // prompts the user typed during the URL ingest.
          if (typeof view._drainQueuedPrompts === "function") view._drainQueuedPrompts();
        }
        try { fs.writeFileSync(urlNewPath, ""); } catch {}
      } else {
        new Notice(`Athena: ${urls.length} URL(s) queued \u2014 open Athena to process.`);
      }
    } catch (e) {
      console.log("[athena] url-new.txt processing error:", e.message);
    }
  }

  /** Watchdog: health check on startup + every 60s. */
  async _watchdogCheck() {
    const vaultPath = this.app.vault.adapter.basePath;
    console.log("[athena] watchdog check");

    // 1. Restart any missing clip watchers. We walk the configured dir
    //    list; any dir that's not already covered in this._clipWatchers
    //    gets a fresh watcher attached (via _setupClipWatcher so the
    //    dedup + handoff logic matches the initial watcher exactly).
    if (!this._clipWatchers) this._clipWatchers = [];
    const covered = new Set(this._clipWatchers.map((w) => w.dir));
    for (const dir of this._resolveClipDirs()) {
      if (!covered.has(dir) && fs.existsSync(dir)) {
        console.log("[athena] watchdog: restarting clip watcher for", dir);
        this._setupClipWatcher(dir);
      }
    }

    const urlNewPath = path.join(vaultPath, "inbox", "url-new.txt");
    if (!this._urlNewWatcher) {
      try {
        if (fs.existsSync(urlNewPath)) {
          console.log("[athena] watchdog: restarting url-new watcher");
          this._urlNewWatcher = fs.watch(urlNewPath, () => this._processUrlNewDebounced());
        }
      } catch (e) { console.log("[athena] watchdog: url-new watcher restart failed:", e.message); }
    }

    // 2. Process pending items
    try {
      if (fs.existsSync(urlNewPath)) {
        const content = fs.readFileSync(urlNewPath, "utf8").trim();
        const urls = content.split("\n").map(l => l.trim()).filter(l => l.startsWith("http"));
        if (urls.length > 0) {
          console.log("[athena] watchdog: found", urls.length, "pending URLs in url-new.txt");
          this._processUrlNewDebounced();
        }
      }
    } catch {}

    try {
      const skipFiles = new Set(["URL Tracker.md", "Add URLs.md"]);
      for (const dir of this._resolveClipDirs()) {
        if (!fs.existsSync(dir)) continue;
        const clips = fs.readdirSync(dir).filter(f => f.endsWith(".md") && !f.startsWith(".") && !skipFiles.has(f));
        if (clips.length > 0) {
          console.log("[athena] watchdog: found", clips.length, "unprocessed clip(s) in", dir);
          for (const clip of clips) {
            this._processClip(dir, clip);
          }
        }
      }
    } catch {}
  }

  /** Watchdog clip processor — simpler than _handleClipFile, used for clips
   *  discovered after the file watcher already fired (or never fired). */
  _processClip(clipDir, filename) {
    const filePath = path.join(clipDir, filename);
    if (!this._processedClips) this._processedClips = new Set();
    if (this._processedClips.has(filePath)) return;
    this._processedClips.add(filePath);
    setTimeout(async () => {
      try {
        if (!fs.existsSync(filePath)) return;
        const view = this._getActiveView();
        if (!view || view.isStreaming) return;
        console.log("[athena] watchdog: processing clip", filename);

        // Watchdog uses a simpler finalization (shorter messages)
        let clipContent = "", clipUrl = "";
        try {
          clipContent = fs.readFileSync(filePath, "utf8");
          const urlMatch = clipContent.match(/source:\s*['"]?(https?:\/\/\S+?)['"]?\s*$/m) ||
                           clipContent.match(/url:\s*['"]?(https?:\/\/\S+?)['"]?\s*$/m);
          if (urlMatch) clipUrl = urlMatch[1];
        } catch {}
        const cleanFilename = filename.replace(/^\(\d+\)\s*/, "");
        const clipName = cleanFilename.replace(".md", "");
        const clipTitleMatch = clipContent.match(/^title:\s*['"]?(.+?)['"]?\s*$/m) || clipContent.match(/^#\s+(.+)/m);
        const clipTitle = clipTitleMatch ? clipTitleMatch[1].trim() : clipName;

        // Same as _handleClipFile — route through process_clip.py to use
        // the canonical URL-derived slug. Watchdog retry path; second clip
        // arriving for the same title would collide on the title-derived
        // bypass. See _handleClipFile for full rationale.
        const vaultBase = this.app.vault.adapter.basePath;
        let rawPath, fullRawPath;
        try {
          const out = execFileSync(pythonCmd(), [
            // Plugin-bundled (1.0.9+) → vault-side fallback.
            resolvePythonScript(this, "bin/lib/process_clip.py"),
            vaultBase,
            filePath,
          ], { encoding: "utf8", timeout: 30000, stdio: ["ignore", "pipe", "pipe"] }).trim();
          fullRawPath = out;
          rawPath = path.relative(vaultBase, fullRawPath);
        } catch (e) {
          const stderr = (e.stderr && e.stderr.toString()) || e.message || String(e);
          console.error("[athena] process_clip failed for retry clip:", filename, stderr);
          new Notice(`Athena: Clip processing failed — ${stderr.split("\n")[0]}`);
          if (this._processedClips) this._processedClips.delete(filePath);
          return;
        }
        const processedDir = path.join(clipDir, ".processed");
        try { fs.mkdirSync(processedDir, { recursive: true }); } catch {}
        try { fs.renameSync(filePath, path.join(processedDir, filename)); } catch {}

        view.addSystemMessage(`New clip received: ${clipName}`);
        view.startStreamingMessage();
        view.isStreaming = true;
        // Per Gryphon issue #3: input stays enabled while clip ingest runs.
        try {
          const result = await this.ingestContent({
            url: clipUrl || null, content: clipContent, title: clipTitle,
            rawPath: rawPath, source: "web-clipper", view,
          });
          if (result.status === "created" && result.pageName) {
            view.finalizeStreamingMessage(`**Page created:** [[${result.pageName}]]`, `Page created: ${result.pageName}`);
          } else if (result.status === "duplicate") {
            view.finalizeStreamingMessage(`**Already in KB:** [[${result.dupPage}]]`, `Already captured`);
          } else {
            view.finalizeStreamingMessage("Clip processed.", "Clip processed");
          }
        } finally {
          view.isStreaming = false;
          // Per Gryphon issue #3: don't re-toggle disabled. Drain queued
          // prompts the user typed during the watchdog clip processing.
          if (typeof view._drainQueuedPrompts === "function") view._drainQueuedPrompts();
        }
      } catch (e) {
        console.error("[athena] _processClip error:", e);
        this._processedClips.delete(filePath);  // allow watchdog retry
      }
    }, 2000);
  }
}

// ── Settings Tab ───────────────────────────────────────────────────

class AthenaSettingTab extends PluginSettingTab {
  constructor(app, plugin) {
    super(app, plugin);
    this.plugin = plugin;
  }

  display(containerEl) {
    // Obsidian passes containerEl, but also sets this.containerEl — accept either
    const el = containerEl || this.containerEl;
    el.empty();
    el.createEl("h2", { text: "Athena Settings" });

    const vaultName = this.app.vault.getName();
    new Setting(el)
      .setName("Web Clipper")
      .setDesc(`Install from obsidian.md/clipper. Vault name: "${vaultName}", Folder: "inbox/clippings"`)
      .addButton((btn) => {
        btn.setButtonText("Copy vault name").onClick(() => {
          navigator.clipboard.writeText(vaultName);
          new Notice(`Copied: "${vaultName}"`);
        });
      });

    new Setting(el)
      .setName("Claude Code CLI path")
      .setDesc("Leave empty to auto-detect.")
      .addText((text) => {
        text.setPlaceholder("Auto-detect")
          .setValue(this.plugin.settings.claudePath)
          .onChange(async (v) => { this.plugin.settings.claudePath = v; await this.plugin.saveSettings(); });
      });

    // Helper for #122: GryphonChatView caches settings at construction, so
    // changing provider/model/effort/permissionMode doesn't refresh the
    // running view's badges or spawn options. Until Gryphon implements
    // reactive settings (polleoai/gryphon#40), we surface a Notice telling
    // the user how to apply the change without losing chat state.
    const showRefreshNotice = (settingName) => {
      new Notice(
        `${settingName} updated. Reopen the Athena chat tab to apply, or toggle the plugin off/on.`,
        7000
      );
    };

    new Setting(el)
      .setName("LLM provider")
      .setDesc("Which backend Gryphon uses to reach the model. \"Auto\" prefers Claude Code if installed, else the first available API key (Anthropic \u2192 OpenAI \u2192 Google).")
      .addDropdown((d) => {
        for (const p of PROVIDER_PREFS) d.addOption(p.value, p.label + " \u2014 " + p.desc);
        d.setValue(this.plugin.settings.providerPreference || "auto")
          .onChange(async (v) => {
            this.plugin.settings.providerPreference = v;
            await this.plugin.saveSettings();
            showRefreshNotice("LLM provider");
            // Re-render the settings tab so downstream provider-dependent
            // sections (model dropdown labels, API-key fields, CLI-path
            // fields, health-check button) reflect the new provider.
            // Without this, the user sees the provider value change but
            // nothing else updates until they reopen the settings tab.
            this.display();
          });
      });

    // Provider-specific Default model dropdown. Mirrors Gryphon's
    // three-branch pattern in vendor/gryphon/src/plugin.js \u2014 without it,
    // OpenAI / Gemini users only see the Anthropic abstract tiers
    // (Haiku / Sonnet / Opus) and the dropdown looks broken because none
    // of those are real OpenAI or Gemini model ids. Auto resolves to the
    // currently-active provider so users with only one key see that
    // provider's native list.
    const { getActiveProviderKind } = require("../../vendor/gryphon/src/providers/factory");
    const _activeKind = getActiveProviderKind(this.plugin) ||
                        this.plugin.settings.providerPreference || "auto";

    if (_activeKind === "google-api" || _activeKind === "gemini-cli") {
      const {
        getModelDropdownOptions: getGeminiOptions,
        resolveModel: resolveGeminiModel,
        DEFAULT_MODEL: GEMINI_DEFAULT_MODEL,
      } = require("../../vendor/gryphon/src/providers/google-api/pricing");
      const geminiModels = getGeminiOptions();
      // Auto-correct stale cross-vendor ids (e.g. "sonnet" carried over
      // from prior Anthropic use) so the displayed dropdown value, the
      // chat toolbar, and runtime model resolution all agree.
      const isKnown = geminiModels.some((o) => o.id === this.plugin.settings.model);
      if (!isKnown) {
        const resolved = resolveGeminiModel(this.plugin.settings.model);
        const fitsDropdown = geminiModels.some((o) => o.id === resolved);
        const persistTarget = fitsDropdown ? resolved : GEMINI_DEFAULT_MODEL;
        if (this.plugin.settings.model !== persistTarget) {
          this.plugin.settings.model = persistTarget;
          this.plugin.saveSettings();
        }
      }
      new Setting(el)
        .setName("Default model")
        .setDesc("Also changeable from the chat toolbar.")
        .addDropdown((d) => {
          for (const m of geminiModels) d.addOption(m.id, m.label);
          d.setValue(this.plugin.settings.model)
            .onChange(async (v) => {
              this.plugin.settings.model = v;
              await this.plugin.saveSettings();
              showRefreshNotice("Default model");
            });
        });
    } else if (_activeKind === "openai-api" || _activeKind === "codex-cli") {
      const {
        getModelDropdownOptions: getOpenAIOptions,
        resolveModel: resolveOpenAIModel,
        DEFAULT_MODEL: OPENAI_DEFAULT_MODEL,
      } = require("../../vendor/gryphon/src/providers/openai-api/pricing");
      const openaiModels = getOpenAIOptions();
      const isKnown = openaiModels.some((o) => o.id === this.plugin.settings.model);
      if (!isKnown) {
        const resolved = resolveOpenAIModel(this.plugin.settings.model);
        const fitsDropdown = openaiModels.some((o) => o.id === resolved);
        const persistTarget = fitsDropdown ? resolved : OPENAI_DEFAULT_MODEL;
        if (this.plugin.settings.model !== persistTarget) {
          this.plugin.settings.model = persistTarget;
          this.plugin.saveSettings();
        }
      }
      new Setting(el)
        .setName("Default model")
        .setDesc("Also changeable from the chat toolbar.")
        .addDropdown((d) => {
          for (const m of openaiModels) d.addOption(m.id, m.label);
          d.setValue(this.plugin.settings.model)
            .onChange(async (v) => {
              this.plugin.settings.model = v;
              await this.plugin.saveSettings();
              showRefreshNotice("Default model");
            });
        });
    } else {
      // Anthropic family (claude-code / anthropic-api / auto-resolves-to-Anthropic)
      // uses the abstract MODELS list \u2014 Gryphon maps these to concrete
      // versions at chat time, so haiku/sonnet/opus/opus[1m] are the
      // right surface here.
      new Setting(el)
        .setName("Default model")
        .setDesc("Also changeable from the chat toolbar. Gryphon resolves these tiers to the latest concrete versions at chat time.")
        .addDropdown((d) => {
          for (const m of MODELS) d.addOption(m.value, m.label + " \u2014 " + m.desc);
          // If the current model is a non-Anthropic id (e.g. user just
          // switched from OpenAI to Anthropic), fall back to "sonnet".
          const _currentValid = MODELS.some((m) => m.value === this.plugin.settings.model);
          d.setValue(_currentValid ? this.plugin.settings.model : "sonnet")
            .onChange(async (v) => {
              this.plugin.settings.model = v;
              await this.plugin.saveSettings();
              showRefreshNotice("Default model");
            });
        });
    }

    new Setting(el)
      .setName("Default effort")
      .addDropdown((d) => {
        for (const e of EFFORTS) d.addOption(e.value, e.label + " \u2014 " + e.desc);
        d.setValue(this.plugin.settings.effort)
          .onChange(async (v) => {
            this.plugin.settings.effort = v;
            await this.plugin.saveSettings();
            showRefreshNotice("Default effort");
          });
      });

    new Setting(el)
      .setName("Default permission mode")
      .setDesc("Safe = auto-accept edits. YOLO = skip all checks. Plan = propose only.")
      .addDropdown((d) => {
        for (const p of PERMS) d.addOption(p.value, p.label + " \u2014 " + p.desc);
        d.setValue(this.plugin.settings.permissionMode)
          .onChange(async (v) => {
            this.plugin.settings.permissionMode = v;
            await this.plugin.saveSettings();
            showRefreshNotice("Default permission mode");
          });
      });

    // Issue #133: connection-timeout override, mirroring Gryphon's tab
    // (Gryphon #38 in v1.4.0). Empty input = use the model-adaptive
    // default \u2014 Haiku 30s, Sonnet 60s, Opus 120s, Opus 1M 180s; non-
    // Anthropic providers 60s. 5\u2013600 second range; out-of-range silently
    // ignored to avoid noisy mid-typing errors. Status line below shows
    // the effective timeout so users see what was accepted.
    let timeoutStatusEl = null;
    const updateTimeoutStatus = (rawInput) => {
      if (!timeoutStatusEl) return;
      const trimmed = (rawInput || "").trim();
      const effectiveMs = resolveConnectionTimeoutMs({
        override: this.plugin.settings.connectionTimeoutMs,
        model: this.plugin.settings.model,
      });
      const effectiveSec = Math.round(effectiveMs / 1000);
      let prefix;
      let color = "";
      if (!trimmed) {
        prefix = `Using model-adaptive default: ${effectiveSec}s`;
      } else {
        const sec = Number(trimmed);
        if (Number.isFinite(sec) && sec >= 5 && sec <= 600) {
          prefix = `\u2713 Override active: ${effectiveSec}s`;
          color = "var(--color-green)";
        } else {
          prefix = `\u2717 Invalid: must be 5\u2013600 seconds. Currently using: ${effectiveSec}s`;
          color = "var(--color-red)";
        }
      }
      timeoutStatusEl.setText(prefix);
      timeoutStatusEl.style.color = color;
    };

    new Setting(el)
      .setName("Connection timeout (seconds)")
      .setDesc(
        "How long to wait for the model's first token before treating " +
        "the request as stuck. Leave empty for the model-adaptive " +
        "default (Haiku 30s, Sonnet 60s, Opus 120s, Opus 1M 180s; " +
        "non-Anthropic providers 60s). Set 5\u2013600 to override for " +
        "slow networks or unusually large prompts."
      )
      .addText((text) => {
        const stored = this.plugin.settings.connectionTimeoutMs;
        const display = (typeof stored === "number" && Number.isFinite(stored) && stored > 0)
          ? String(Math.round(stored / 1000))
          : "";
        text
          .setPlaceholder("default")
          .setValue(display)
          .onChange(async (value) => {
            const trimmed = (value || "").trim();
            if (!trimmed) {
              this.plugin.settings.connectionTimeoutMs = null;
              await this.plugin.saveSettings();
              updateTimeoutStatus(value);
              return;
            }
            const sec = Number(trimmed);
            if (Number.isFinite(sec) && sec >= 5 && sec <= 600) {
              this.plugin.settings.connectionTimeoutMs = Math.round(sec) * 1000;
              await this.plugin.saveSettings();
            }
            // Out-of-range or non-numeric: don't persist. Status line
            // below shows the validation error AND the effective fallback
            // so the user sees their input was rejected.
            updateTimeoutStatus(value);
          });
      })
      .then((setting) => {
        timeoutStatusEl = setting.descEl.createDiv({ cls: "setting-item-description" });
        timeoutStatusEl.style.marginTop = "4px";
        timeoutStatusEl.style.fontStyle = "italic";
        const stored = this.plugin.settings.connectionTimeoutMs;
        const initialDisplay = (typeof stored === "number" && Number.isFinite(stored) && stored > 0)
          ? String(Math.round(stored / 1000))
          : "";
        updateTimeoutStatus(initialDisplay);
      });

    new Setting(el)
      .setName("Open in main tab")
      .setDesc("Open chat in a main tab instead of the right sidebar.")
      .addToggle((t) => {
        t.setValue(this.plugin.settings.openInMainTab)
          .onChange(async (v) => { this.plugin.settings.openInMainTab = v; await this.plugin.saveSettings(); });
      });

    new Setting(el)
      .setName("Web Clipper folders")
      .setDesc(
        "Comma-separated list of folders (relative to vault root) " +
        "Athena watches for new Web Clipper files. Defaults cover the " +
        "Obsidian Web Clipper extension's factory default (clippings/) " +
        "and the legacy path (inbox/Clippings). Change takes effect " +
        "after restarting Obsidian."
      )
      .addText((t) => {
        t.setPlaceholder("clippings, inbox/Clippings")
          .setValue(this.plugin.settings.clippingsFolder || "clippings, inbox/Clippings")
          .onChange(async (v) => {
            this.plugin.settings.clippingsFolder = v;
            await this.plugin.saveSettings();
          });
      });

    // Provider-aware test button. Routes the health check based on the
    // selected LLM provider (#117). For CLI providers, spawn `<cli> --version`.
    // For API providers, surface an info message \u2014 Athena doesn't have the
    // credentials needed to run a real API health check (Gryphon owns those).
    el.createEl("h3", { text: "Test" });
    const testBtn = el.createEl("button", { text: "Test Connection" });
    testBtn.addEventListener("click", async () => {
      const provider = this.plugin.settings.providerPreference || "auto";

      // Map provider \u2192 { label, binaryFinder, defaultName, args }.
      // For "auto", try claude first (matches Gryphon's auto-fallback chain
      // which prefers Claude Code if installed).
      const cliMap = {
        "claude-code": { label: "Claude Code", bin: this.plugin.settings.claudePath || findClaudeBinary(), name: "claude" },
        "codex-cli":   { label: "Codex CLI",   bin: "codex", name: "codex" },
        "gemini-cli":  { label: "Gemini CLI",  bin: "gemini", name: "gemini" },
        "auto":        { label: "Claude Code (auto)", bin: this.plugin.settings.claudePath || findClaudeBinary(), name: "claude" },
      };
      const apiSet = new Set(["anthropic-api", "openai-api", "google-api"]);

      if (apiSet.has(provider)) {
        new Notice(`Provider '${provider}' uses an HTTP API. Health check requires sending a chat message \u2014 try one to verify the key works.`, 8000);
        testBtn.textContent = `${provider}: send a chat to verify`;
        return;
      }

      const cli = cliMap[provider];
      if (!cli || !cli.bin) {
        new Notice(`${cli?.label || provider} CLI not found on PATH. Install it or change provider in Settings.`);
        testBtn.textContent = `\u2717 ${cli?.label || provider} not found`;
        return;
      }

      testBtn.disabled = true;
      testBtn.textContent = "Testing...";
      const proc = spawn(cli.bin, ["--version"]);
      const timer = setTimeout(() => { try { proc.kill(); } catch {} }, 5000);
      let out = "";
      proc.stdout.on("data", (d) => { out += d.toString(); });
      proc.on("close", (code) => {
        clearTimeout(timer);
        testBtn.disabled = false;
        testBtn.textContent = code === 0 ? `\u2713 ${cli.label} ${out.trim()}` : `\u2717 ${cli.label} failed`;
      });
      proc.on("error", () => { clearTimeout(timer); testBtn.disabled = false; testBtn.textContent = `\u2717 ${cli.label} not found`; });
    });
  }
}

// ── Search Modal ───────────────────────────────────────────────────

class SearchModal extends Modal {
  constructor(app, vaultPath) {
    super(app);
    this.vaultPath = vaultPath;
    this.query = "";
  }

  onOpen() {
    const { contentEl } = this;
    contentEl.createEl("h2", { text: "Athena Search" });

    new Setting(contentEl)
      .setName("Query")
      .addText((text) => {
        text.setPlaceholder("Search your knowledge base...");
        text.inputEl.style.width = "100%";
        text.onChange((v) => (this.query = v.trim()));
        text.inputEl.addEventListener("keydown", (e) => {
          if (e.key === "Enter") this.runSearch();
        });
        setTimeout(() => text.inputEl.focus(), 50);
      });

    new Setting(contentEl)
      .addButton((btn) => {
        btn.setButtonText("Search").setCta().onClick(() => this.runSearch());
      })
      .addButton((btn) => {
        btn.setButtonText("Close").onClick(() => this.close());
      });
  }

  runSearch() {
    if (!this.query) { new Notice("Enter a search query"); return; }

    new Notice("Searching...");
    const kbPath = path.join(this.vaultPath, "bin", "kb");
    execFile(kbPath, ["search", this.query], {
      cwd: this.vaultPath,
      timeout: 30000,
      env: { ...process.env, PATH: buildEnhancedPath() },
    }, (error, stdout, stderr) => {
      if (error) {
        new Notice("Search failed: " + (stderr || error.message).substring(0, 80));
        return;
      }

      const resultsPath = "wiki/dashboards/Search Results.md";
      const file = this.app.vault.getAbstractFileByPath(resultsPath);
      if (file) {
        this.app.workspace.getLeaf().openFile(file);
      } else {
        new Notice("Results written \u2014 open Search Results.md");
      }
      this.close();
    });
  }

  onClose() {
    this.contentEl.empty();
  }
}

// ── Setup Wizard ───────────────────────────────────────────────────

class AthenaSetupWizard extends Modal {
  constructor(app, vaultName) {
    super(app);
    this.vaultName = vaultName;
  }

  onOpen() {
    const { contentEl } = this;
    contentEl.addClass("athena-setup-wizard");

    contentEl.createEl("h1", { text: "Welcome to Athena" });
    contentEl.createEl("p", { text: "Your Second Brain is ready. Let's set up web clipping so you can capture pages from your browser." });

    contentEl.createEl("h2", { text: "Step 1: Install Web Clipper" });
    const installP = contentEl.createEl("p");
    installP.createEl("span", { text: "Install the " });
    installP.createEl("strong", { text: "Obsidian Web Clipper" });
    installP.createEl("span", { text: " (not the Notion one) for your browser:" });
    const linkP = contentEl.createEl("p");
    linkP.createEl("a", { text: "https://obsidian.md/clipper", href: "https://obsidian.md/clipper" });
    contentEl.createEl("p", { text: "After installing, open the Web Clipper settings in your browser." });

    contentEl.createEl("h2", { text: "Step 2: General settings" });
    contentEl.createEl("p", { text: "In the Web Clipper General settings, find the Vaults section. Add this vault name:" });

    const vaultRow = contentEl.createDiv("athena-setup-row");
    vaultRow.createEl("code", { text: this.vaultName, cls: "athena-setup-value" });
    const copyVaultBtn = vaultRow.createEl("button", { text: "Copy", cls: "athena-setup-copy" });
    copyVaultBtn.addEventListener("click", () => {
      navigator.clipboard.writeText(this.vaultName);
      copyVaultBtn.textContent = "Copied!";
      setTimeout(() => { copyVaultBtn.textContent = "Copy"; }, 1500);
    });

    contentEl.createEl("h2", { text: "Step 3: Set note folder" });
    contentEl.createEl("p", { text: "In the Web Clipper template settings, set the folder to:" });

    const folderRow = contentEl.createDiv("athena-setup-row");
    folderRow.createEl("code", { text: "inbox/Clippings", cls: "athena-setup-value" });
    const copyFolderBtn = folderRow.createEl("button", { text: "Copy", cls: "athena-setup-copy" });
    copyFolderBtn.addEventListener("click", () => {
      navigator.clipboard.writeText("inbox/Clippings");
      copyFolderBtn.textContent = "Copied!";
      setTimeout(() => { copyFolderBtn.textContent = "Copy"; }, 1500);
    });

    contentEl.createEl("h2", { text: "Step 4: Behavior" });
    contentEl.createEl("p", { text: "In the Behavior section, look for these settings and set them as shown. If an option listed here doesn't match what you see, just skip it \u2014 defaults are fine." });

    const table = contentEl.createEl("table", { cls: "athena-setup-table" });
    const header = table.createEl("tr");
    header.createEl("th", { text: "Setting" });
    header.createEl("th", { text: "Recommended" });

    const settings = [
      ["Save clipped note without opening it", "ON"],
      ["Legacy mode", "OFF"],
    ];
    for (const [name, value] of settings) {
      const row = table.createEl("tr");
      row.createEl("td", { text: name });
      row.createEl("td", { text: value, cls: "athena-setup-value" });
    }

    contentEl.createEl("h2", { text: "Step 5: Start clipping" });
    contentEl.createEl("p", { text: "Open any page in your browser and click the Web Clipper icon to save it. Then in Athena, type:" });
    contentEl.createEl("code", { text: "kb add", cls: "athena-setup-value" });
    contentEl.createEl("p", { text: "This processes all pending pages (clippings + queued URLs) into your knowledge base." });

    const doneBtn = contentEl.createEl("button", { text: "Done \u2014 Start using Athena", cls: "athena-setup-done" });
    doneBtn.addEventListener("click", () => this.close());
  }

  onClose() {
    this.contentEl.empty();
  }
}

module.exports = AthenaPlugin;
