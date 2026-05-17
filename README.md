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
- **Optional**: Python 3.10+ for full wiki synthesis (see below).

## Capture-only mode vs. full synthesis mode

Athena ships in two operating modes depending on what's present in your vault:

| Mode | What's in the vault | What `kb add <url>` does |
|---|---|---|
| **Capture-only** (Community Plugins / BRAT default) | The plugin alone — no `bin/` or `config/` | Browser-captures the page, saves a raw markdown file under `raw/<type>/artifacts/`. No wiki page synthesis. Chat surfaces an honest "Raw saved — wiki synthesis was skipped" message with a link to the full vault. |
| **Full synthesis** | The plugin + Athena's Python backend (`bin/` + `config/` from [polleoai/athena](https://github.com/polleoai/athena)) + Python 3.10+ on the user's system | Capture + LLM-driven wiki page synthesis with `## Key Findings` / `## Methods / Architecture` / `## Notable Quotes` / `## Relevance` sections, cross-referencing against existing pages, lint-as-enforcement on every change. Also unlocks the full `kb` command set (`kb search`, `kb query`, `kb reflect`, `kb merge`, etc.) from the terminal. |

Community Plugins users get capture-only by default — that's intentional. The Python backend is heavy (LLM calls, file walking, lint suite) and tracking the full Athena vault as a submodule of every user's personal vault would be unreasonable. The roadmap to bring wiki synthesis into the JS plugin (so it works on a vanilla Community Plugins install) is tracked as the 1.1 milestone.

To run in full synthesis mode today: clone [polleoai/athena](https://github.com/polleoai/athena) and open the cloned directory as an Obsidian vault. The bundled plugin loads from `.obsidian/plugins/athena/` inside that vault and immediately has access to `bin/` and `config/` siblings.

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
