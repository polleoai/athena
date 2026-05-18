# Athena

Your Second Brain — in Obsidian.

Athena turns your Obsidian vault into a personal knowledge base that compounds over time. Ingest URLs, papers, GitHub repos, screenshots, and videos; an LLM synthesizes each one into a structured wiki page, cross-references it with what you already have, and keeps the whole graph navigable, searchable, and chat-queryable. Athena bundles [Gryphon](https://github.com/polleoai/gryphon) for the chat surface, so you can ask questions about your knowledge base using the LLM provider of your choice (Anthropic, OpenAI, Google, or a local CLI).

> Athena is not affiliated with Anthropic, OpenAI, Google, or Obsidian. References to those services describe what the plugin and its bundled SDKs talk to; they do not imply endorsement.

## Features

- **`kb` command set** — one verb-driven CLI for everything: ingest a URL (`kb add`), search (`kb search`), ask (`kb query`), reflect on recent journal entries (`kb reflect`), organize hubs (`kb create`, `kb move`, `kb merge`), prune (`kb remove`, `kb trash`, `kb purge`, `kb undo`). Available in-vault through Gryphon chat or directly in the terminal.
- **Three-layer architecture** — immutable `raw/` sources, LLM-maintained `wiki/` synthesis, and a schema (this repo's `CLAUDE.md`) that governs LLM behavior. Sources are never modified; wiki pages can be regenerated; the schema is the contract.
- **Structured wiki digests** — every wiki page has `## Key Findings`, `## Methods / Architecture`, `## Notable Quotes`, and `## Relevance` sections derived from the raw source by an LLM you choose. No 50K-character body copies.
- **Bidirectional cross-references** — when page A links to page B, page B links back. Frontmatter `related:` for graph view and search; body `## Connections` section for human navigation.
- **Soft delete with rollback** — `kb remove` and `kb merge` move files to `.kb-trash/` where they stay for 30 days. `kb undo` restores. `kb purge` permanently deletes. Even after purge, the original URL is preserved so the source can be recaptured from scratch.
- **Self-healing capture pipeline** — Web Clipper drops, terminal `kb add`, and Athena's own `kb capture-deep` (Playwright-based) all flow into the same ingest path. LinkedIn auto-promotes to the deep-capture path so JS-rendered content lands intact.
- **Lint as enforcement** — every data inconsistency identified in production becomes an auto-fixing check in `kb lint`. The lint set is the permanent safety net; LLM instructions are suggestions.
- **Chat with your KB** — Athena bundles Gryphon. Talk to your knowledge base through the chat panel in your sidebar; Gryphon's built-in security layer protects `.git/`, `.obsidian/`, `.env`, and other dangerous paths/commands even in YOLO mode.

## Installation

**Via Obsidian's Community Plugins directory** — once the submission is accepted, search for "Athena" in Obsidian → Settings → Community plugins → Browse.

**Via BRAT** *(for early access to release candidates)* — the [Beta Reviewers Auto-update Tool](https://github.com/TfTHacker/obsidian42-brat) is a community plugin that installs plugins directly from GitHub releases.

1. Install BRAT from Obsidian's Community Plugins directory.
2. Open **Settings → BRAT → Add Beta Plugin**.
3. Paste `polleoai/athena` and click "Add Plugin".
4. Enable Athena in **Settings → Community plugins**.

**From source** *(for contributors)*:

1. Clone the repo into the parent directory of your Obsidian vault, then symlink or copy the built bundle to `.obsidian/plugins/athena/` inside the vault.
2. Run `npm install` and `npm run build:all` (the `:all` variant builds the bundled Gryphon submodule too).
3. In Obsidian → Settings → Community plugins → enable Athena.

## Requirements

- **Obsidian 1.0.0 or later** (desktop only — Athena's terminal `kb` commands and Python KB engine need a desktop filesystem and Node child-process spawning).
- **At least one LLM provider** for the bundled Gryphon chat surface:
  - **Anthropic API key** from [console.anthropic.com](https://console.anthropic.com) (default, recommended), **or**
  - An OpenAI / Google Gemini API key, **or**
  - A locally-installed `claude` CLI (advanced opt-in — confirm your intended usage complies with that product's Commercial Terms).
- **Optional**: Python 3.10+ for the wiki page builder + bulk-regen utilities (see below).

## How `kb add` works

The plugin runs end-to-end synthesis on every supported platform (macOS, Linux, Windows):

1. **Capture** — fetches the page into `raw/<type>/artifacts/{slug}.md`
2. **Wiki page** — `bin/lib/wiki_page.py` writes a wiki page with frontmatter + Source/Local-Copy line + structure
3. **Synthesis** (1.1+) — Athena calls the LLM you have configured in **Gryphon settings** (Claude Code CLI, Anthropic / OpenAI / Google API, or Codex / Gemini CLIs) and rewrites the page body with `## Key Findings` / `## Methods / Architecture` / `## Notable Quotes` / `## Relevance`

If synthesis can't run (no LLM configured), the page is still created with a "Pending synthesis" placeholder and you'll see `*Synthesis skipped: no LLM provider configured*` in the chat reply. Fix it with `kb regen <url>` once Gryphon is set up.

### Wiki page builder needs Python (still)

The wiki-page scaffolding step (step 2 above) is performed by `python3 bin/lib/wiki_page.py`. As of 1.0.9, the plugin bundle ships Athena's Python sources (`bin/lib/*.py` + `bin/config/athena.default.json`) inside the plugin install dir — earlier releases required cloning the full Athena vault. Community Plugins users just need:

- **macOS**: `brew install python` (3.10+) or [python.org/downloads](https://www.python.org/downloads/)
- **Windows**: [python.org/downloads](https://www.python.org/downloads/) — IMPORTANT: check "Add python.exe to PATH"
- **Linux**: usually pre-installed; `python3 --version` to verify

Then one-time:
```bash
python -m pip install pydantic
```
Restart Obsidian. `kb add` runs end-to-end. The 1.2 milestone tracks porting the wiki-page builder to JS so this final Python dependency disappears too.

### Power users: clone the full Athena vault

The full [polleoai/athena](https://github.com/polleoai/athena) repo carries additional terminal-only `kb` commands (`kb search`, `kb query`, `kb reflect`, `kb merge`, etc.), a lint suite (`kb lint`), and the bundled raw + wiki layers that make Athena a working knowledge base out of the box. Clone the repo and open the cloned directory as an Obsidian vault to use those.

## Known limitations

### Pages that block browser automation

Sites behind Cloudflare's bot challenge, JavaScript-rendered SPAs that require interaction, and authenticated content (LinkedIn timeline, X.com feed) often return a challenge page instead of the article when captured headlessly. Athena will save the challenge-page content as the raw file in those cases (not what you wanted). **Workaround**: open the page in your normal browser, use the [Obsidian Web Clipper extension](https://obsidian.md/clipper) to drop a clipping into `<vault>/clippings/`, and Athena's watcher will pick it up.

### Linux: BrowserWindow sandbox can be slow on first try

On Ubuntu (especially 22.04+ with Snap-installed Obsidian under AppArmor), Chromium's sandbox helper sometimes fails to start the renderer for an Electron `BrowserWindow`. Athena's 15s outer timeout (added in 1.0.3) makes this a graceful 15s fall-through to the `<webview>` capture path rather than a permanent hang, but the first capture on a fresh Linux install can take up to 15s longer than on macOS / Windows. Subsequent captures usually work normally as the sandbox helper warms up.

### Prompt history across Obsidian restarts

Up-arrow recall (Gryphon's terminal-style prompt history) reads from the persisted `chat-history.json` on Obsidian launch. If you sent a prompt right before quitting Obsidian — especially while a slow operation was still in flight — the persistence may not have flushed yet, and that prompt won't be in the up-arrow walk after restart. The visible chat bubble survives (it's loaded from the same file, which DID save before quit); only the input-history index doesn't see it. Open issue under investigation for 1.1.

## How it works — three layers

| Layer | What lives here | Who writes it |
|---|---|---|
| **Raw** (`raw/`) | Immutable source material — clipped webpages, PDFs, repo snapshots, screenshots, video transcripts | Ingest pipeline only; the LLM never modifies raw sources |
| **Wiki** (`wiki/`) | LLM-maintained synthesis pages — one per source, plus topic / entity / comparison pages cross-referencing them | LLM, via `kb` commands |
| **Schema** (`CLAUDE.md`) | Operating principles, command interface, safety rules | Maintainer |

When you run `kb add <url>`, Athena fetches the page into `raw/`, then generates a corresponding `wiki/` page with structured sections, then cross-references it against existing pages. The wiki is the layer you read and chat with; the raw layer is the audit trail.

## Network endpoints

Athena's plugin runtime makes network calls only through the bundled Gryphon chat surface, which in turn talks to the LLM provider you've configured in **Settings → Gryphon**:

| Provider mode | Endpoint(s) contacted | Triggered by |
|---|---|---|
| Anthropic API (default) | `https://api.anthropic.com` | Chat messages, tool-use loop |
| OpenAI | `https://api.openai.com` | Chat messages, tool-use loop |
| Google Gemini | `https://generativelanguage.googleapis.com` | Chat messages, tool-use loop |
| Claude Code (CLI) | None over the network from Athena — the CLI subprocess makes its own outbound calls | Chat messages, tool-use loop |

The terminal `kb` command set (run outside Obsidian, in a shell) makes additional outbound network calls when the user explicitly invokes them:

- **`kb add <url>`** — fetches the URL via `curl`/`wget` (any host the user provides). For JS-heavy domains on a known allowlist (github.com, x.com, linkedin.com, youtube.com, arxiv.org, medium.com, etc.) it may launch a sandboxed Chromium via Playwright. The full allowlist is in `bin/lib/capture.py`.
- **`kb add` with arXiv / HuggingFace / GitHub URLs** — may follow canonical-source URLs derived from the captured page (arXiv abstract pages → PDFs, GitHub blob URLs → raw, etc.). This is the auto-discovery path documented in CLAUDE.md as "discover-and-surface, never auto-fetch new ingestion" — discovered sources are queued, not auto-pulled.
- **`kb report-bug` / `kb request-feature`** — opens a GitHub issue on the upstream Athena repo via `gh` CLI.
- **Embedding calls** (if configured) — local Ollama at `http://localhost:11434` only. Athena never sends embedding requests to a remote service.

Browser-based capture (Playwright) runs with Chromium's security sandbox enabled. The terminal `kb` commands never navigate to `file://` URLs, `localhost`, or private IPs.

## System identity reads

Athena reads the following system properties at runtime, in both the plugin and the terminal path:

- **Vault root path** (via `app.vault.adapter.basePath` in the plugin; via the script's working directory in the terminal). Required so the Python KB engine knows where to read/write `raw/` and `wiki/`.
- **Operating system family** (via Node's `process.platform`) — only used to choose between macOS-specific paths (`launchd`) and Linux/Windows fallbacks in `scripts/`. Not transmitted.
- **`os.hostname()` and `os.userInfo()`** are read by bundled Gryphon for CLI-mode auto-detection (see Gryphon's own README for full disclosure). Athena itself does not read these.
- **Environment variables** consulted: `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY` (Gryphon), `ATHENA_DISABLE_PLAYWRIGHT_PROMOTE` (test escape hatch for the LinkedIn auto-promote path). All optional; absence falls back to in-app settings.

## Privacy and data handling

- **All knowledge lives on your local filesystem**, inside your Obsidian vault. Athena never uploads your vault contents to any external service.
- **LLM calls send page contents** (raw source body, wiki page body, or query context) to your configured provider — that's the cost of using an LLM. If you don't want a third party to see specific pages, don't add them to the KB, or use the local CLI provider mode.
- **`.kb-trash/`** is local-only; nothing leaves your machine when you delete.
- **Per-machine plugin state** — `data.json` (API keys) and `chat-history.json*` for both Athena and Gryphon are gitignored. They never enter the repo.
- **No telemetry by default.** `kb report-bug` only sends data when you explicitly run it, and shows you the payload before transmission (see CLAUDE.md operating principle #6 for the data-integrity feedback loop spec).

## Build from source

```bash
git clone --recurse-submodules https://github.com/polleoai/athena.git
cd athena
npm install
npm run build:all     # builds bundled Gryphon plugin too, then Athena's bundle
```

Build outputs land in `.obsidian/plugins/athena/`:

- `main.js` — Athena's plugin bundle (~1.3 MB, includes Gryphon)
- `manifest.json` — plugin manifest
- `styles.css` — bundled stylesheet (sourced from Gryphon — Athena adds none of its own)

To pre-flight a release, run `scripts/release-smoke-test.sh`. The `--fresh` variant clones into `/tmp` first so it exactly mirrors what Obsidian's submission scorecard sees.

## Author

[POLLEO.AI](https://www.polleo.ai)

## License

See `LICENSE` if present. Bundled Gryphon retains its own license (see `vendor/gryphon/LICENSE`).
