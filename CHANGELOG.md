# Athena — Changelog

All notable changes to the Athena Obsidian plugin are documented here. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versioning is [SemVer](https://semver.org/).

## [Unreleased]

## [1.7.6] — 2026-08-02

Headline: **the bundled assistant moves to Gryphon 2.9.6, and the plugin builds cleanly from a fresh checkout.** This release picks up Gryphon's dependency-security and housekeeping updates (2.9.3 → 2.9.6) and fixes source builds in a clean environment. Athena's own capture and synthesis behaviour is unchanged.

### Security

- **Bundled dependencies updated to clear security advisories.** Moving the bundled Gryphon assistant to 2.9.6 pulls in its dependency fixes: the Anthropic SDK was moved off a version carrying a moderate advisory (CVE-2026-41686), and seven further advisories in the dependency tree — six of them high severity, including a WebSocket library used by the Google and OpenAI clients that could disclose uninitialised memory — were resolved. Athena's own `@anthropic-ai/sdk` build dependency was updated to match. No change to how the plugin behaves.

### Fixed

- **The plugin now builds from a clean checkout.** `npm install && npm run build` on a fresh clone previously failed because the build bundles the vendored Gryphon assistant's source, which lives in a git submodule that a plain clone leaves empty. The build now initializes that submodule automatically when it is missing, so a fresh checkout builds without extra steps. No effect on the released plugin's behaviour.

## [1.7.5] — 2026-07-31

Headline: **the bundled assistant gains a new provider, retires a discontinued one, hardens its guardrails, and adds four models.** This release moves the bundled Gryphon assistant from 2.8.0 (shipped in Athena 1.7.1) to 2.9.3. Athena's own capture and synthesis behaviour is unchanged.

### Added

- **New assistant provider: Antigravity CLI.** You can now run the bundled assistant through Google's Antigravity CLI (`agy`) — pick **Antigravity CLI (advanced)** in Settings → Models. Gryphon auto-detects the binary (with a detection status and a **Re-detect** button, plus a field for a non-standard install path), and the CLI handles its own sign-in — run `agy` once in a terminal, no API key to enter. Antigravity uses the Gemini model list and is also available as a fallback provider.
- **Four new models.** Claude Opus 5, GPT-5.6 Terra, GPT-5.6 Luna, and Gemini 3.6 Flash are now selectable from the model dropdown, each with current pricing.

### Changed

- **The discontinued Gemini CLI provider is retired.** Google ended Gemini CLI access for Gemini Code Assist individual accounts; that failure used to be invisible — the assistant would appear to work and simply return an empty reply. The provider is now hard-deprecated with a clear pointer to Antigravity CLI as its replacement. The Gemini API provider (your own API key) is unaffected.

### Security

- **Antigravity runs under Gryphon's approval layer — and now refuses to run if it can't.** Antigravity has no per-request approval prompt that works outside an interactive terminal, so Gryphon takes over that decision with its own approval layer. On Windows in Gryphon 2.9.1–2.9.2 that layer could be installed but never actually start, and Antigravity treats a layer that fails to start as permission granted — so protected commands and paths were not enforced, on Windows only. That is fixed, and Gryphon now stops with an explanation naming the cause rather than continuing unprotected. More of Antigravity's file and command actions are recognised, so protected paths hold whichever route the model takes — including a blocked file write retried as a shell command.

### Fixed

- **Antigravity CLI is detected reliably on slower machines.** Its unusually large program file could exceed the version-check window and be reported as "not detected" even when installed; the window now scales with the program size. CLI detection checks also no longer leave background processes behind on Windows.

## [1.7.1] — 2026-07-06

Headline: **the bundled assistant gains new, more capable models.** This release moves the bundled Gryphon assistant to 2.8.0, which adds Anthropic's Claude Fable 5 and Sonnet 5 and Google's Gemini 3.5 Flash and 3.1 Flash-Lite to the model dropdown, and makes Sonnet 5 the new default for the Claude path. Athena's own capture and synthesis behaviour is unchanged from 1.7.0.

### Added

- **New Claude and Gemini models in the bundled assistant.** Gryphon 2.8.0 adds Claude Fable 5 (Anthropic's most capable model, for demanding reasoning and long-horizon work) and Claude Sonnet 5 (the newest balanced model, at the same price as Sonnet 4.6), plus Gemini 3.5 Flash (tuned for agentic and coding work) and Gemini 3.1 Flash-Lite (the cheapest Gemini 3 tier). All are selectable from the model dropdown; the Claude and Gemini paths use your own API key as before.

### Changed

- **Claude Sonnet 5 is the new default model.** Fresh installs start on Sonnet 5. If you have already picked a model, your choice is unchanged and every previous model (Sonnet 4.6, the Opus tiers, and the rest) remains selectable.

## [1.7.0] — 2026-07-05

Headline: **capture quality — papers keep their full text, and LinkedIn/X posts come in clean.** Saved papers now carry the whole PDF, not just the abstract; LinkedIn and X captures are stripped of feed scaffolding so what you keep is the post and its comments; and the health check now audits your social and paper captures for the ones worth re-capturing. No change to the bundled assistant (Gryphon 2.7.0).

### Added

- **The health check now audits your captures.** `kb lint` flags LinkedIn/X posts that came in thin, chrome-laden, or truncated, and papers saved abstract-only — so you can spot the low-quality ones and re-capture them, instead of finding out later when a summary is missing half the content.

### Fixed

- **Saved papers now include the full text, not just the abstract.** Clipping or adding an arXiv paper downloaded the PDF but saved only the abstract into the local copy, so the summary and search saw a fraction of the paper. Athena now reads the whole PDF and saves the complete text alongside the abstract. The health check lists older papers still saved abstract-only so you can re-capture them.
- **Clipping a PDF paper no longer shows a spurious "failed."** A PDF you clipped from the browser can't be read as a web page, so Athena re-fetches it in the background — but when two of its watchers raced to capture the same paper, the second one reported a failure even though the paper had already been saved correctly. Re-capturing an already-saved paper is now treated as success, so the false "failed" is gone.
- **Cleaner saved LinkedIn posts.** A LinkedIn post clipped from the browser used to drag in a pile of feed scaffolding — the byline "profile card", a profile-image link before every commenter, the share menu (Copy / LinkedIn / Facebook / X), "Report this post/comment" links, bare reaction counts, the "X is an Influencer" badge, and stray timestamps — sometimes more chrome than post. All of it is now stripped at capture, so the saved copy is the post and the comment text (including the arXiv/GitHub links people drop in the comments, which are preserved and queued for capture).

## [1.6.5] — 2026-07-03

Headline: **the bundled assistant gets safer, and slide-deck talks capture cleanly.** This release moves the bundled Gryphon assistant to 2.7.0, which now asks before running package-install commands and flags reverse-shell command shapes — closing two common ways a booby-trapped project tries to get code onto your machine. reveal.js slide-deck extraction moves to the upstream capture engine (more reliable, and nested vertical slides are now captured), and re-captures no longer spawn duplicate pages.

### Security

- **The bundled assistant now blocks package installs by default.** Gryphon 2.7.0 flags commands that install software (`pip`, `npm` / `pnpm` / `yarn` / `bun`, `gem`, `cargo`, `go`, `brew`, `apt` / `dnf` / `yum`, `conda`, `poetry`, and similar) for your approval before they run — even when you've set auto-approve — because working in your vault never needs to install software. This closes a common attack: an innocent-looking "install the requirements" step that quietly does more than it claims. A **Block package installation** switch (Settings → Security) is on by default; turn it off if a workflow genuinely needs to install packages.
- **Reverse-shell and payload-staging commands are now recognized.** Command shapes used to open a reverse shell — a live connection that hands remote control of your machine to someone else — plus the DNS lookups sometimes used to fetch a hidden payload, now surface for your approval instead of running silently. Ordinary interactive shells and everyday commands are unaffected.

### Changed

- **Slide-deck talk pages capture more reliably.** reveal.js decks now extract through the upstream capture engine instead of an Athena-side fallback — cleaner per-slide text, and nested vertical slides (a slide inside a slide) are captured instead of dropped or merged.

### Fixed

- **Re-capturing a page no longer creates a duplicate.** When you re-clip or re-add a source and the title comes out slightly different the second time, Athena used to create a second page for the same source. It now recognizes the existing page by its underlying saved file and updates that one instead of spawning a duplicate.

## [1.6.3] — 2026-07-01

Headline: **cleaner saved web articles.** Fetched pages used to drag in the surrounding page chrome — the "share this page" bar (X / LinkedIn / Facebook / email buttons), the article title repeated two or three times with a "| Site Name" masthead, a section breadcrumb, and stray empty bullets. v1.6.3 trims all of that at capture, so a saved article opens on the article. Athena-only patch; bundled assistant unchanged (Gryphon 2.6.1).

### Fixed

- **"Share this page" buttons no longer land in your saved copy.** Clipping an article whose page has a social-share bar (X / LinkedIn / Facebook / email "share" buttons) used to pull those share links into the saved text as a stray list. They're now stripped at capture, so the saved copy is just the article.
- **Cleaner saved articles — no repeated title, masthead, or breadcrumb.** Fetched web articles sometimes carried the page's own header into the saved copy: the title shown two or three times (once with a "| Site Name" masthead), a site-section breadcrumb, and empty leftover bullets. That chrome is now trimmed so the saved copy opens on the actual article, and the page title drops the trailing site name.

## [1.6.2] — 2026-07-01

Headline: **LinkedIn posts capture and link correctly.** Clipping a LinkedIn post that came from the share button used to save nothing (or a "clip processing failed" error) and, when it did save, left a Source link that opened LinkedIn's "Invalid post link" page. This release captures the post from the exact address you opened, records that resolvable link as the Source, and makes re-clipping an existing page heal its link in place. Athena-only patch; no change to the bundled assistant (Gryphon 2.6.1).

### Fixed

- **LinkedIn posts capture again.** Clipping or adding a LinkedIn post whose link came from the share button (or an in-app share) could land nothing — Athena saved LinkedIn's "Invalid post link" error page instead of the post. Athena now fetches the exact address your browser opened, so the post, its text, and its images come through. (A shared post that previously showed a recurring "clip processing failed" error now captures cleanly.)
- **The Source link on a LinkedIn page now opens the post.** The saved page recorded an internal identifier as its Source link, which LinkedIn rejects with "Invalid post link." Athena now records the resolvable address you clipped, while still using the identifier behind the scenes to avoid duplicate pages. Older LinkedIn pages captured before this fix keep the broken link (the original address wasn't saved) — the health check now lists them so you can re-clip, and re-processing a page picks up a corrected link.
- **No more phantom "clip processing failed" error.** When a clip was already handled by one background pass, a second pass could find the file gone and surface an alarming "clip not found" error. That case is now treated as already-done and stays silent.
- **Re-clipping a page to fix its link now just works.** Re-clipping an already-saved page corrected the download but left the page's visible Source link untouched, so it looked like nothing happened. A re-clip now heals the page's Source link in place from the freshly-captured address — no separate refresh needed.

## [1.6.1] — 2026-06-27

Headline: **pages built from embedded slides now capture their content, clips that used to fail now recover, and a security-hardened assistant.** When you clip a page whose real content lives in an embedded slide deck or document, Athena now pulls that embedded content in as its own page instead of saving an empty shell. Clips of script-heavy pages that previously failed with an error now fall back to fetching the page directly. Bundles **Gryphon 2.6.1**, which hardens the assistant against prompt injection on the Anthropic path.

### Added

- **Embedded content is captured.** A page can embed its substance from another address (a slide deck, an embedded document). Athena now detects those embeds, captures each as its own page, and cross-links it to the page it came from — so the content is searchable instead of invisible. Slide decks are saved slide-by-slide as clean, readable text.

### Fixed

- **Clips of script-heavy pages no longer get stuck.** A clip that came through empty (common for pages that render their content dynamically) used to fail repeatedly and surface a recurring "clip processing failed" error. Athena now recovers by fetching the page directly, and only sets a clip aside if that also fails — so the error no longer repeats.
- **Sites that only serve from their `www` address now capture.** Some sites don't respond at the bare domain; capture now retries the `www` form automatically.
- **Clearer duplicate detection.** The health check now names the specific pages involved when two pages point at the same source, making cleanup obvious.
- **Synthesis no longer leaves a worker behind.** After a page is synthesized, the background worker is now reaped instead of lingering, so repeated captures don't accumulate stray processes.

### Changed

- **Vendored Gryphon 2.6.0 → 2.6.1.** Prompt-injection hardening on the Anthropic API path. No change to provider failover, the readiness check, or the existing security guardrails.

## [1.6.0] — 2026-06-23

Headline: **a clearer way to set up your AI provider — redesigned settings.** The provider, model, and API-key controls Athena embeds from the bundled assistant (Gryphon) are now organized into a **Models / Advanced** tab strip: your provider choice, its credentials, and the per-session defaults sit together under **Models**, with niche tuning knobs tucked into **Advanced**. Same fields, grouped so the primary setup path is obvious and the rarely-touched options stay out of the way. Bundles **Gryphon 2.6.0**.

### Changed

- **Redesigned provider & model settings (tabbed).** The embedded provider surface now renders as **Models / Advanced** tabs instead of one long list. The Models tab groups provider selection, scoped credentials, per-session defaults, and the fallback provider; Advanced holds the Brave key, auto-compact, auto-retry, connection timeout, and max file size. No setting was removed — every control from 1.5.1 is still here.
- **Vendored Gryphon 2.5.1 → 2.6.0.** Brings the redesigned settings interface plus shorter command names in the palette and an internal code-quality pass. No behaviour change to provider failover, the readiness check, or the security guardrails.
- **Athena's provider settings refresh themselves on a provider switch.** Switching provider inside the embedded surface now updates the provider-dependent credential fields on its own; Athena no longer drives that refresh from the host side.

## [1.5.1] — 2026-06-22

Headline: **bundled-assistant maintenance — Obsidian plugin-guideline compliance.** A housekeeping release. The bundled assistant (Gryphon) had an Obsidian community-review pass: shorter command names, and its timer, configuration-folder, and command-registration code brought in line with the plugin guidelines. No behaviour change — every command, provider failover, the readiness check, and the security guardrails all work exactly as in 1.5.0. Bundles **Gryphon 2.5.1**.

### Changed

- **Vendored Gryphon 2.5.0 → 2.5.1.** Obsidian plugin-guideline compliance pass (timer usage, config-folder handling, command registration) plus shorter command names in the palette. No behaviour change.
- **Athena resolves its plugin folder via the vault's configured config directory** instead of a hardcoded `.obsidian` fallback, so a renamed Obsidian config folder is handled correctly. No behaviour change for default setups.

## [1.5.0] — 2026-06-21

Headline: **see your knowledge, not just store it — Bases, Canvas, and per-project journals.** Athena already captures and synthesizes everything you read into a typed, cross-linked vault. This release adds three ways to *view* it in Obsidian's own surfaces — and fixes provider setup so you can configure any AI provider directly from Athena. Bundles **Gryphon 2.5.0**.

### Added

- **`kb bases` — your knowledge as live, filterable tables.** Generates Obsidian Bases (`.base`) over each typed collection (papers, repos, videos, webpages, entities) plus an "All Knowledge" dashboard, into `wiki/bases/`. Each is a sortable table built from that type's own fields (title, tags, date, link). Re-run any time; it only rewrites what changed.
- **`kb canvas <topic>` — a visual map of how a topic connects.** Renders a topic's cross-references as an Obsidian Canvas (`.canvas`): the topic in the center, ringed by the pages it links to, colored by type. Turns the connections Athena builds as you read into something you can actually see, into `wiki/canvas/`.
- **`kb journal --project "X"` — journals that know which project they belong to.** Scopes a journal entry to a project (`wiki/journal/X/`) and seeds a retrospective template (done / data / problems / learnings / tomorrow / what's worth keeping). `kb reflect --project "X"` reflects on just that project. Plain `kb journal` is unchanged.

### Fixed

- **You can now set any AI provider's API key from Athena's own settings.** Athena's settings tab previously left out the API-key fields, so anyone using a key-based provider — Gemini, OpenAI, or the Anthropic API — had no way to configure it and the assistant simply wouldn't run. Athena now renders the full provider, model, and key configuration directly, so every provider is set up in one place.

### Changed

- **Bundles Gryphon 2.5.0.** Brings a configurable fallback provider (Gryphon retries on a backup when your main provider is rate-limited or briefly unavailable, and tells you which one answered) and a readiness check that flags a missing key or uninstalled CLI *before* you send — both now surfaced inside Athena's settings.

### Notes

- Bases and Canvas write native Obsidian files into your vault; regenerating snapshots the prior version to `.kb-trash/` first, so nothing is lost. Empty collections are skipped.

## [1.4.3] — 2026-06-21

Headline: **capture and ingest keep working even when your system Python is broken.** If your computer's Python was missing a piece (a mismatched install — common after an OS or Homebrew update), clipping a page would silently do nothing: the clip landed but never became a note. Athena already builds its own clean, isolated Python when you run `kb doctor --fix` — but until now nothing actually used it for the command-line ingest. Now Athena detects a broken interpreter and automatically switches to its own healthy one, so capture and ingest just work. Bundles **Gryphon 2.4.5** (unchanged).

### Fixed

- **Ingest no longer dies on a broken system Python.** When the Python that Athena's `kb` commands run under can't load its dependencies (e.g. Homebrew Python 3.14 with a mismatched core library), Athena now automatically falls back to the clean, isolated Python it provisions via `kb doctor --fix` — instead of crashing partway through and leaving your clipped page unprocessed. This covers every path: the command line, the Obsidian plugin's auto-ingest, and deep web capture.
- **`kb doctor --fix` now confirms the repair is wired up.** After it provisions the isolated Python, it tells you Athena will use it automatically — no manual environment variables to set.

## [1.4.2] — 2026-06-20

Headline: **switching your AI provider or model now takes effect on your next message.** Changing the provider (Claude → Codex / Gemini), model, reasoning effort, or permission mode in Athena's settings used to relabel the toolbar while the next message was still answered by the previously-running provider — so a switch made to work around one provider's usage limit was silently ignored. That's fixed. Bundles **Gryphon 2.4.5**.

### Fixed

- **Provider and model switches take effect on your next message.** When you change the AI provider, model, reasoning effort, or permission mode from Athena's settings, the chat now retires the active session so your new choice is the one that answers — instead of quietly continuing with the previous provider. The settings notice now says so plainly ("takes effect on your next message") instead of telling you to reopen the tab.

### Changed

- **Vendored Gryphon → 2.4.5.** Picks up the provider/model-switch fix above, plus a release-packaging fix so the bundled assistant always ships its current code rather than a stale build.

## [1.4.0] — 2026-06-17

Headline: **setup heals itself and X Articles capture in full.** A new `kb doctor` checks and repairs your environment in one command, the bundled assistant auto-selects a working CLI instead of hanging on a stale one, and a curated set of Web Clipper templates makes browser capture land cleanly. Bundles **Gryphon 2.4.3**.

### Added

- **`kb doctor` — one-command environment check and repair.** `kb doctor` validates your Python and dependencies by actually importing them — not by trusting version numbers, which can lie when two installs shadow each other — and reports exactly what's wrong. `kb doctor --fix` provisions an isolated, self-consistent environment that Athena uses automatically, leaving your system Python untouched. If no working Python is found at startup, the plugin now surfaces a one-line pointer to the fix instead of failing silently. A declared support matrix (Python 3.11+, no upper cap) documents what each release targets.
- **Athena Web Clipper templates.** A curated template set for the official Obsidian Web Clipper — general pages, LinkedIn, X/Twitter posts, and X Articles — now ships with the plugin. Clips arrive tagged with the right title, source URL, and capture method so Athena ingests them cleanly instead of guessing.
- **Full X Article capture.** Long-form X (Twitter) Articles now capture with their real structure — headings, lists, and inline images saved locally — instead of a flattened, image-less preview.

### Changed

- **Vendored Gryphon → 2.4.3.** The embedded assistant now picks a working command-line tool (Claude / Codex / Gemini) by probing versions and choosing the newest valid one — healing itself when a stale tool happens to be found first on the system. This fixes first-run chat hangs that previously surfaced only as a 90-second connection timeout. When no usable tool exists, it now fails in about a second with a clear, actionable message instead of hanging.

### Fixed

- **Picks a working Python.** Athena now selects a Python interpreter that actually runs its dependencies, instead of blindly using whichever `python3` the system resolves first — important when a system Python is broken, shadowed, or too new for an installed package set.
- **Clips ingest even when the panel is closed.** A web clip is now picked up and turned into a page whether or not the Athena panel is open.
- **X/Twitter capture.** Tweets re-sync through the reliable syndication path (a brittle page selector was dropped); handle-based Article URLs (`/<handle>/article/…`) are now recognized; image-heavy social clips keep their images locally.
- **Windows stability.** Settings are initialized before the first command can race them, and the startup environment check runs without blocking the interface on slower machines.

## [1.3.0] — 2026-06-15

Headline: **GitHub repos and X/Twitter posts now capture cleanly** with purpose-built extractors instead of the generic page reader, and Athena runs correctly on **native Windows**. Bundles **Gryphon 2.4.0**.

### Added

- **Dedicated GitHub repository capture.** Clipping or adding a GitHub repo now pulls the real README through GitHub's API — so the README's own images and content thumbnails come through intact, instead of the generic DOM reader's mangled, fixed-width image dump. Falls back to a normal page capture if the repo can't be reached.
- **Dedicated X/Twitter capture.** Tweets and long-form X Articles are captured via X's public syndication endpoint — full post text, the author, media, and (for Articles) the title, cover, and preview — instead of the truncated link-only snippet the page reader produced.

### Fixed

- **Windows: pages landed in the wrong category.** On native Windows, a captured video (or other typed source) could have its wiki page written under *Webpages* instead of its correct section (Videos, Repos, …) because of a path-separator mismatch. Pages now land in the right place on every OS. A `kb lint` check surfaces any previously-miscategorized page for relocation.

### Security

- Hardened the new capture paths against untrusted remote content: newline-stripping before writing page frontmatter (prevents metadata injection), a guard so a crafted README image can't point off-repository, response-size caps, and stricter tweet-URL matching.

### Changed

- **Vendored Gryphon → 2.4.0.** The public build bundles the current Gryphon (headless provider-backend session API).

## [1.2.4] — 2026-06-05

Consolidates 1.2.1–1.2.4 (the intermediate bumps were vendored-Gryphon refreshes). Headline: the `kb` engine is now **native cross-platform Python**, and **LinkedIn document/carousel posts capture in full**.

### Added

- **Native cross-platform `kb` engine.** The entire `kb` command set *and* `bin/kb-capture` are ported from Bash to Python and run natively on **Windows, Linux, and macOS** — validated end-to-end on a 3-OS VM matrix. `ATHENA_PYTHON` env override lets you point the engine at a specific interpreter/venv (used by self-provisioning test guests). A frozen Bash oracle (`bin/kb-legacy`) backs a byte-for-byte parity test suite.
- **LinkedIn document & carousel capture.** A clip of a LinkedIn document/carousel post now auto-promotes to a Playwright deep-capture that paginates the carousel, and `kb lint` embeds the captured slides into the wiki page in document order.
- **`kb add-content`** — ingest pre-captured content (title + text) directly, bypassing fetch. For pages an automated fetch can't reach (auth-blocked); paste the content and Athena builds the wiki page.
- **Thin-share tweet resolution.** A tweet that is just a pointer to another tweet now surfaces the *destination* tweet instead of dropping it (C1/C2/C3).
- **`unified_ingest`** — a single routing/dispatch entry point behind every ingest path (Web Clipper, `kb add`, `url-new.txt`, paste, orphan reconciliation).

### Changed

- **arcus floor pinned to `>=0.6.0`** (resolves from PyPI). See 1.2.0 for the arcus cutover itself.
- **Search-first wiki lookup** — page lookups go through the prebuilt index; dropped watchdog log spam.

### Fixed

- **Capture / titles:** YouTube ingest is clean end-to-end (comments stripped, description captured, stable `video-<id>` slug); X.com `"<name> on X: …"` title chrome stripped; embedded image URLs no longer leak into wiki titles; Obsidian-forbidden characters stripped from page names; conference PDF titles expand acronyms and stop truncating at 65 chars; file slugs stop truncating at 60 chars and dropping the tail.
- **Data integrity / lint:** dead-URL records that were later captured are cleared (resurrection check); the dead-URL ledger is rewritten atomically; merge/rename filenames keep the `Prefix: Title` colon on POSIX; the raw-frontmatter reader lint check covers every module; URL Tracker links resolve to real pages; the default-config hash warning no longer fires on legitimate committed edits; thin-clip auto-fix trashes the matching asset directory atomically.
- Frontmatter scalars are kept on a single physical line.

## [1.2.0] — 2026-05-18

**arcus cutover (Plan A.2b)**. Athena's per-format content extraction (HTML pages incl. X.com tweets, PDFs, DOCX/XLSX/PPTX/EPUB, YouTube transcripts) now goes through the new [arcus](https://github.com/jivebug/arcus) content-extraction kernel. Athena owns vault-aware orchestration; arcus owns single-source extraction.

### Added

- **`arcus-provider-runtime[html,pdf,office]` as a required dependency** (`server/pyproject.toml`). Install: `pip install --user --break-system-packages -e ~/Projects/arcus/packages/provider-runtime[html,pdf,office]` plus `python3 -m playwright install chromium`.
- **`bin/lib/arcus_html.py`** — adapter for HTML / X.com tweet extraction via arcus's HtmlProvider. Used by kb-capture's webpage + tweet + LinkedIn-fallback branches. `--print-body` mode returns text-only for the upstream tweet/LinkedIn pre-fetch paths.
- **`bin/lib/arcus_file.py`** — adapter for PDF / DOCX / XLSX / PPTX / EPUB extraction via arcus's PdfProvider + DocsProvider. Used by `bin/ingest-file`.
- **`bin/lib/url_detect.py`** — athena-only URL routing (verbatim lift of `detect_type` from the deleted `file_extract.py`). Knows `github→repo`, `arxiv→paper`, `drive→paper`, etc. — concepts arcus has no awareness of.

### Removed (~1900 LOC)

- `bin/lib/fetch-page.py` (1241 LOC) — superseded by arcus's HtmlProvider
- `bin/lib/fetch-tweet.py` (59 LOC) — superseded by arcus's HtmlProvider xcom path
- `bin/lib/file_extract.py` (433 LOC) — split: routing → `url_detect.py`; extraction → arcus
- `bin/lib/html2md.mjs` (202 LOC) — vendored inside arcus's `providers/html/`
- All four files removed from the `.obsidian/plugins/athena/bin/lib/` bundle too.

### Changed

- `bin/kb-capture` shrinks by 108 LOC. The webpage branch's html2md.mjs + Playwright fallback 2-tier ladder collapses to one `arcus_html.py --print-body` call. Tweet + LinkedIn pre-fetch paths route through the same adapter.
- `bin/ingest-file` shrinks by 21 LOC. The post-download extract_text + _write_raw block becomes a single `arcus_file.ingest_local_file()` call. Download logic stays in athena (vault-aware: Drive special-handling, dead-URL recording, magic-byte validation).
- `CLAUDE.md` documents the arcus integration + install prerequisites + the athena-vs-arcus responsibility split.

## [1.1.1] — 2026-05-18

Release-process cleanup version. No functional or API change vs 1.1.0; this release exists to re-trigger Obsidian's Community Plugins directory automated review with the cleaned-up release-asset set.

### Changed — Release assets

- **Drop install zip from GitHub Releases**. Obsidian's directory only fetches `main.js`, `manifest.json`, and `styles.css`; additional release files surfaced a "Recommendation" warning on the 1.1.0 submission. The workflow no longer produces or attaches `athena-X.Y.Z.zip`. The Python backend (required for full synthesis) is still available by cloning the repo source.
- **Drop `bin/` packaging from the workflow**. Was only consumed by the install zip. Stage-assets step is now exactly the three Obsidian-supported files.
- **Drop the zip from `actions/attest-build-provenance` subject list**. Attestations now cover only the three supported assets.

### Workflow hardening (also in 1.1.1 release window — landed before the version bump)

- **CHANGELOG-extraction fallback**: when `awk` can't find a `## [X.Y.Z]` section in `CHANGELOG.md`, the workflow now emits a default release note linking to the full CHANGELOG + commit-history. Previously this exited 1 and the release was never created — caught by the 1.0.1 backfill failure.
- **`FORCE_JAVASCRIPT_ACTIONS_TO_NODE24=true`** at workflow `env:` level. GitHub forces Node 24 for JavaScript actions on 2026-06-02; this opts in now and silences the deprecation warning emitted by `actions/checkout@v4`, `actions/setup-node@v4`, `actions/attest*`, and `softprops/action-gh-release@v2`.
- **README**: removed the "Pushing a tag to the polleoai/athena public mirror" subsection. Maintainer-only knowledge that didn't belong in the public README's "Known limitations" block.

### Why this release exists

Obsidian's docs ([Submit your plugin](https://docs.obsidian.md/Plugins/Releasing/Submit+your+plugin)) state that to address feedback from the automated review, the submitter must "update your repository and publish a new GitHub release with an incremented version." 1.1.0 had a passing review modulo one "Recommendation" warning about the bundled install zip. This release ships a clean automated-review surface.

## [1.1.0 — release pass] — 2026-05-18

Final code review + QA pass before locking 1.1.0 as the canonical release. Three classes of issue surfaced:

### Fixed — Windows UnicodeEncodeError on lone surrogates

The atomic wiki-page write at `bin/lib/wiki_page.py` crashed with `UnicodeEncodeError: 'utf-8' codec can't encode character '\udc8d'` when upstream content contained an unpaired UTF-16 surrogate (e.g. from `errors='surrogateescape'` decoding of mojibake bytes). Wiki pages and raw rewrites now run through a new `_safe_utf8()` helper that replaces lone surrogates with U+FFFD before the write and logs to stderr. The atomic write succeeds; one byte of bad data becomes the replacement character. Four regression tests pin the contract.

### Fixed — defensive quote-strip on URL args in `kb add` / `kb refresh` / `kb regen`

The same regex-capture-with-quotes pattern that caused `kb remove "Foo Bar"` to fail in 1.1.0 also lurked in three URL-taking handlers in `_runKbCommandAsync`. A user typing `kb add "https://x.com/foo"` landed with literal quote chars in `args[0]`, poisoning `canonicalize()` and every downstream lookup. All three now strip surrounding quotes the same way `_kbRemove` / `_kbRename` do.

### Fixed — two stale tests in `TestCreateWikiPageOverwrite`

The two `overwrite=True` tests asserted that `llm_result.body` flowed into the wiki page. The current architecture (synthesis-stub pages, body lives in `raw/`) makes that assertion always false — the wiki page is a placeholder until LLM synthesis fills it in. Tests now assert the `summary:` field delta (which IS written into the frontmatter) to prove the rewrite happened. Test contract is unchanged; only the assertion surface moved to the right field.

### Investigation — Windows LLM-spawn `>` bug

The earlier report (`> was unexpected at this time.` from cmd.exe during LLM CLI spawn) traced to Gryphon's `wrapForCmdShim` (vendor/gryphon/packages/protect/src/win-spawn.js). The vendor escape path DOES caret-escape `>` to `^>` before the cmd.exe `/c` dispatch (see win-spawn.js:143), and all three CLI providers (Claude Code, Codex, Gemini) route through this wrap on Windows. No consumer-side fix is appropriate without modifying vendor — and per `feedback-gryphon-vendoring-principle`, vendor stays read-only. If the bug re-surfaces, capture stderr + spawn args to root-cause.

### Test suite

`tests/test_writers.py`: 110 passing + 1 skipped (Playwright auto-promote needs a browser context). 4 new tests cover the surrogate-sanitization contract.

## [1.1.0] — 2026-05-17

### Added — JS-side wiki synthesis (cross-platform)

`kb add <url>` now generates the structured digest (`## Key Findings` / `## Methods / Architecture` / `## Notable Quotes` / `## Relevance`) inline as the final step of the capture flow, on every platform Athena runs on. The macOS-only launchd dependency (`com.athena.autoingest`) is no longer required to fill in the "Pending synthesis" placeholder.

Synthesis is routed through **Gryphon's provider abstraction** — whichever LLM the user has configured for chat (Claude Code CLI, Anthropic / OpenAI / Google API, or Codex / Gemini CLIs) is what generates the digest. Claude Code subscription stays free; API users pay per-token. No new configuration surface: there is no second model selector in Athena, no second API-key field.

### New module set under `src/athena/`

- **`synthesis.js`** — orchestrator. Builds the user prompt, calls Gryphon's `createProvider(plugin, vaultPath, { resumeSessionId: null })`, parses JSON response, runs quality checks with retry, writes back. Also exports `findPendingPages(plugin)` for bulk-regen scanning.
- **`synthesis-quality.js`** — verbatim port of the Python `quality_check_summary` + `quality_check_body`: forbidden-opener list (12 entries), forbidden marketing-word list (6), length bounds (100–600 chars), no-newline rule, unicode-math-bold leak check, mandatory `## Key Findings` section. Pure functions, ~10 unit tests pass.
- **`synthesis-wiki-update.js`** — frontmatter parser, raw-body loader (prefers `raw_path:` over wiki body — ground truth), `summary:` field rewriter, body-zone splicer. The body zone is anchored between the canonical "Local Copy" line and the first `## Connections` / `## Keywords` section, so Connections/Keywords are preserved verbatim across regens.

### New command: `kb regen`

- `kb regen <url>` — re-runs synthesis on an existing page. Useful for backfilling pages stuck at the placeholder (Windows installs from 1.0.x), or refreshing after the raw source changes.
- `kb regen --all-pending` — scans `wiki/format/**` for every page still showing `*Pending synthesis*` and regenerates them in sequence. Stops early on `no-provider` so the user isn't held through N identical failures.

Both go through the same pattern-matched dispatch as other JS-implemented kb verbs — the LLM never sees the raw `kb regen` text.

### Changed

- `ingestContent` return value gained a `synthesis: { ok, reason, ... }` field; the kb add chat reply surfaces synthesis failure as `*Synthesis failed: <reason>. Run kb regen <url> to retry.*`
- README "Operating modes (as of 1.0.9)" rewritten as "How `kb add` works" — capture-only is no longer the default Windows experience; synthesis works everywhere with a Gryphon provider configured.
- README "Known limitations (1.0) — Wiki synthesis needs Python" section removed; Python is still needed for the wiki-page builder (1.2 milestone tracks porting it too), but the synthesis step itself is now Python-free.

### Why this matters

Pre-1.1 Athena on Windows produced wiki pages stuck at `*Pending synthesis — run kb regen to generate.*` permanently, because the launchd job that fires the Python regen script doesn't exist on Windows. The user saw a page that looked broken and a command (`kb regen`) that didn't exist. Both fixed: `kb regen` now exists as a real command, and synthesis runs inline by default so the placeholder never reaches the user in normal use.

The pattern of "Athena depends on macOS-specific automation" is gone from the capture flow. The remaining Python dependency (`bin/lib/wiki_page.py` for the page scaffolding) is on the 1.2 roadmap.

### Also in 1.1 — browser-extractor DOM walker

Replaced the `el.innerText`-based capture in `_BROWSER_EXTRACT_JS` with a proper recursive DOM walker. The old extractor stripped structural markup: `<ul><li>` became newline-separated text with no `-` markers, `<pre><code>` became plain text with no fences, and CSS-line-broken `<span>`-per-glyph math (X.com's inline LaTeX rendering) became one character per line.

The walker emits markdown for the 10 element types that dominate technical content (headings, paragraphs, lists, code, blockquote, links, bold/italic, tables, breaks). Three post-processors clean up the worst residue: math-explosion collapse (concatenates runs of ≥3 single-character lines drawn from Mathematical Alphanumeric Symbols / Greek / math operators), Unicode-bullet normalization (•, ‧, ⁃, ◦, ▪, ►, → → `- `), and blank-run collapse (≥3 newlines → 2).

Bundle grew by ~8 kb. 5/5 functional checks pass for lists, code fences, formula collapse, and no exploded-single-char-line residue. The 1.2 milestone tracks bringing in Turndown + Defuddle for the long tail of capture edge cases.

## [1.1.0 — late] — orphan-raw reconciliation: mtime gate (refined)

The earlier opt-in env-var gate was too aggressive — it disabled
orphan-raw scanning entirely, which broke the auto-wiki-create flow
for FRESH captures (Web Clipper drop → raw written → no wiki page
because scan was skipped). User-reported via `insomnia_vip` clip not
appearing in latest-20 dashboard.

Refined: scan only raws modified in the last 10 min (configurable
via `ATHENA_RECONCILE_MAX_AGE_MIN`). Fresh captures qualify, stale
orphans don't. The original `ATHENA_RECONCILE_ORPHANS=1` env var
still works as a legacy opt-out of the mtime gate (scan everything).

## [1.1.0 — late] — orphan-raw reconciliation opt-in

User-surfaced bug: a Web Clipper drop of one URL triggered a cascade of
4 unwanted wiki pages because `bin/kb add` (no args) was scanning ALL
orphan raw files on every run and auto-creating wiki pages for them.
The "orphan raws" came from a previous-session `kb add` that survived
an early-version `kb remove`. The launchd auto-ingest watcher fires
`kb add` on every clipping drop, so the reconcile cascade fired
whenever any new clip landed.

Fix: gate the orphan-raw scan in `bin/kb add` behind an env var
(`ATHENA_RECONCILE_ORPHANS=1`). Default behavior is now "process new
clippings only" — matches the user's mental model of "I clip one, I
get one." Orphan reconciliation still happens via `kb lint` (lint #2,
explicit user-triggered).

The canonical-source auto-discovery from social posts (arXiv, GitHub,
DOI, PDF URLs extracted by `canonical_source.py` and queued in
`inbox/url-new.txt`) is UNCHANGED — that's the desired behavior per
the user's spec ("auto ingest should add more pages if the page
mentioned a repo or the source of the paper").

## [1.0.17] — 2026-05-17

### Added — full kb-command consolidation (phase 1)

Architectural fix: every kb verb from CLAUDE.md is now pattern-matched in `src/athena/kb-commands.js`, so the LLM **never sees `kb` syntax as a prompt to interpret**. Previously, only ~15 verbs (`add`, `lint`, `stats`, `list`, `search`, etc.) were pattern-matched; the rest (`remove`, `merge`, `move`, `ungroup`, `rename`, `create`, `insight`, etc.) fell through to the LLM, which then explored the codebase for 7+ turns to infer behavior from CLAUDE.md and the source code before executing via raw filesystem tool calls. Slow, expensive, non-deterministic.

All verbs now have patterns. Handler dispatch tiers:

**Tier 1 — full JS implementation** (new in this release; cross-platform, no Python/bash dependency):
- `kb remove <page>` — soft-deletes wiki page + linked raw to `.kb-trash/<ts>_kb-remove/`, updates `inbox/url-resolved.tsv` status `captured` → `removed`. Reversible via `kb undo`.
- `kb undo` — restores the latest `.kb-trash/<ts>_*/` bundle to its original locations, reverts TSV status `removed` → `captured` for any restored wiki page. Cleans up the empty bundle dir after.
- `kb trash` — lists all `.kb-trash/` bundles newest-first with file counts, ages, and purge-eligibility flag (≥ 30d).
- `kb rename <page> --to "New Name"` — renames the wiki page file (applying Windows-safe filename sanitization) AND updates `[[oldName]]` → `[[New Name]]` wikilinks across all `wiki/**/*.md` + `inbox/**/*.md` files. Skips `raw/` (raw bodies don't carry wikilinks). Reports how many files were updated.

**Tier 2 — pattern-matched, routes to existing `bin/kb` spawn**:
- `kb merge`, `kb move`, `kb ungroup`, `kb create`, `kb insight`, `kb reflect`, `kb query`, `kb refresh-wiki`, `kb config`, `kb rules add`, `kb report-bug`, `kb request-feature`
- Works on macOS / Linux today (calls into `bin/kb` bash script). On Windows these surface a "command requires bash backend" error from `runMechanical` — clear failure mode instead of LLM exploration.

**Tier 3 — already pattern-matched + handled** (unchanged): `add`, `refresh`, `lint`, `stats`, `index`, `list`, `search`, `journal`, `rules` (read), `trash`, `undo`, `purge`.

### Why this matters

A `kb remove` chat invocation that used to be 7 LLM turns + ~15 tool calls + a few seconds of inference is now **one synchronous JS function call**, executes in milliseconds, behaves identically every time. The LLM is freed to do what it's actually good at — synthesis, reasoning, exploration in unfamiliar territory — instead of repeatedly inferring well-defined Athena semantics.

### Phase 2 (future release)

JS implementations for the Tier 2 verbs (`merge`, `move`, `ungroup`, `create`, etc.) so Windows users get full cross-platform parity. Tier 1 unblocks the most common cleanup workflows today; the rest stay POSIX-bash for now with a clear failure path.

## [1.0.16] — 2026-05-17

### Fixed — chat `kb add` extracts real page title and images (was text-only)

The browser-capture path executed one bit of JS that returned `document.body.innerText` — no `<title>`, no images, no metadata. Two visible consequences:

- **Every raw had `title: "Page"`** (or `"X Post"` / `"Git — repo"` per-host fallback) regardless of the actual page title. Web Clipper captures had real titles; chat `kb add` never did.
- **Image-heavy pages (Cisco blog, Medium, etc.) had zero images** in the raw markdown. The "Local Copy" link opened a wall of text with no visuals.

Fix: `_browserWindowCapture` and `_webviewCapture` now share a `_BROWSER_EXTRACT_JS` script that returns `{ title, text, images: [{src, alt}] }` in one round-trip:

- **Title**: pulled from `<title>` element. Plugin strips trailing site-name chrome (` | LinkedIn`, ` — Cisco Blogs`, etc.) before storing. Per-host fallback (`X Post`, `Git — repo`, `Page`) only fires when no `<title>` exists.
- **Text**: same progression as before — X tweets first, then `article` / `main` / `[role="main"]`, then `document.body`. No behavior change.
- **Images**: scoped to the main content area (`article` / `main` / `[role="main"]`, falls back to `body`). Filters: HTTPS only (no `data:` / `blob:` / relative); skip < 50px width or height to drop tracking pixels and favicons; dedupe by src. Emitted as HTML `<img src="..." alt="..." width="600">` for parity with 1.0.13's twimg cleanup — same width constraint, works in both Obsidian Edit Mode and Reading View.

### Use case

You captured `https://blogs.cisco.com/ai/ai-defense-explorer-lab` via chat `kb add`. **Before 1.0.16**: raw file had `title: "Page"` and zero images, Local Copy was a text wall. **1.0.16+**: raw has `title: "Try Cisco AI Defense Explorer Edition in this hands-on lab"` (or whatever Cisco's `<title>` was, minus the trailing " — Cisco Blogs") and all article images embedded as 600px-wide `<img>` tags.

### Refresh existing captures

The fix only affects NEW captures. Existing pages with `title: "Page"` stay as-is unless explicitly regenerated. Re-capture cleanly:

```
kb remove <page-name>
kb add <url>
```

Or for the wiki page only (won't re-capture the raw, so the raw still has the old title/no-images):

```
kb refresh <url>
```

(The `kb refresh` command from 1.0.15 only regenerates the wiki layer, not the raw. To get the new title + images, you need a fresh capture.)

## [1.0.15] — 2026-05-17

### Added — `kb refresh <url>` chat command (Windows-compatible page regeneration)

Stale wiki pages (created before a YAML / wikilink / filename fix landed) could not be regenerated through normal `kb add` because the dedup check returned `'exists'` early. The dedup message suggested typing `"update <page>"` but that path only triggered when new content was attached — a plain `kb add <url>` had no escape hatch. The POSIX-only `kb refresh-wiki` bash command existed but doesn't run on Windows where `bin/kb` is bash.

New JS-side chat command `kb refresh <url>` closes the gap:

- **Looks up the page by URL** via `inbox/url-resolved.tsv` (the canonical index — bypasses the filesystem walk).
- **Reads the wiki page's `raw_path:` frontmatter** to find the existing raw source.
- **Calls `wiki_page.py --stdin` with `overwrite=true`** so the Python builder rewrites the wiki page in place. The raw file is NOT re-captured — only the wiki layer is regenerated using the current code's YAML formatter, wikilink syntax, sanitization rules, etc. For a fresh browser fetch, use `kb remove` + `kb add` instead.
- **Reversible** — the Python builder snapshots the prior wiki page to `.kb-trash/` before writing; `kb undo` restores.
- **Works on Windows** — pure JS plugin path, no `bin/kb` bash dependency.

Companion change in `bin/lib/wiki_page.py`: the `--stdin` JSON handler now accepts an `overwrite` boolean field (defaults to `false` for backward compat) and forwards it to `create_wiki_page`. Without this, the JS `_runWikiPageBuilder({...overwrite: true})` call would have silently dropped the flag.

### Use case

You captured a page weeks ago when Athena was 1.0.10. The page's YAML has backslash paths (1.0.12 fix didn't exist yet). Obsidian shows "Invalid properties" banner. Type in Athena chat:

```
kb refresh https://blogs.cisco.com/ai/ai-defense-explorer-lab
```

Page regenerated with 1.0.14's forward-slash YAML + Local Copy wikilink. Banner clears. No manual delete required.

## [1.0.14] — 2026-05-17

### Fixed — Windows: broken "Local Copy" wikilink in wiki page body

1.0.12 normalized backslash separators in the `raw_path:` *frontmatter* field but missed the `[[raw\webpages\artifacts\...|Local Copy]]` wikilink in the page body (line 951 in build_wiki_page). On Windows the unnormalized form had backslashes, which **Obsidian's wikilink resolver doesn't follow** — clicking offered to CREATE a new note. Repeated clicks produced auto-numbered empty stubs (`...md`, `... 1.md`, `... 2.md`, `... 3.md`, `... 4.md`) cluttering the vault. Fix: `local_copy.replace('\\', '/')` before writing the wikilink, matching what 1.0.12 already did for `raw_path:`.

### Fixed — X.com video overlays empty body content

Web Clipper captures X.com `<video>` elements with two compounding problems: (1) the `<source>` `src` is `blob:https://x.com/...` — a page-local URL that won't resolve outside the original tweet's runtime, so the video renders as an empty box; (2) the element carries inline `style="position: absolute; top: 0%; left: 0%; ..."` which makes that empty box **overlay whatever scrolls into the same screen region** — body text disappears underneath. New `_strip_blob_videos(body, source_url)` in `process_clip.py` removes any `<video>...<source src="blob:...">...</video>` block and replaces it with a `[Watch video on source](url)` link. Web Clipper's sibling poster `<img>` (the video's still frame, already correctly rewritten by 1.0.13's twimg fix) survives — the visual is preserved, the broken overlay is gone, the user clicks through to the original tweet for the actual playback.

### Migration note for pages stuck with old YAML

If you have wiki pages that were created BEFORE 1.0.12 and show "Invalid properties" in Obsidian, the dedup detection prevents normal `kb add` from regenerating them. Two paths to fix:

- **`kb refresh-wiki <page>`** — re-processes the raw source and rewrites the wiki body with the current code's YAML formatter. Snapshot of the prior page goes to `.kb-trash/` so the rewrite is reversible via `kb undo`.
- **Delete + re-add**: `kb remove <page>` then `kb add <url>` (forces a fresh capture).

The new YAML formatter only runs at page-create time, not on every read; that's why "Invalid properties" persists for stale pages even when the plugin itself is up to date.

## [1.0.13] — 2026-05-17

### Fixed — X.com images now constrained to 600px width in Edit Mode too

1.0.12's twimg cleanup used Obsidian's markdown alt-text width syntax `![Image|600](url)`. Turns out **Obsidian honors `|width` only in Reading View** — Live Preview / Edit Mode renders external image markdown at native pixel size with no width constraint, even with the `|600` in alt text. A `name=medium` image (~1200px wide) still overwhelms the typical 800px editor pane. User correctly reported "the editing mode shows the same problem. if i change to read-only mode, the display is correct."

Switched to HTML `<img>` tag with explicit `width="600"` — honored in **both** Reading View and Edit Mode (and identically by every other markdown renderer):

- **`bin/lib/process_clip.py`** `_rewrite_twimg_images` now produces `<img src="...name=medium" alt="..." width="600">` instead of `![alt|600](...)`. Two regex branches: markdown `![](pbs.twimg.com/...)` → HTML, and existing HTML `<img src="pbs.twimg.com/...">` → canonicalized HTML (strips Web Clipper junk attributes like `tabindex`, `disableremoteplayback`, inline styles).
- **`scripts/rewrite-twimg-sizes.py`** got the same overhaul plus a cleanup pass for stale `|600` artifacts in HTML alt attributes (leftover from the brief 1.0.12 markdown-pipe form). Re-running is idempotent — already-canonical refs are skipped.
- **Dev vault: 60 image refs across 9 X.com captures rewritten to the new HTML form.**

### Trade-off

HTML img is less portable than markdown image syntax (some markdown processors don't render `<img>` tags). For Obsidian — which is Athena's primary surface — HTML img is the correct choice. Other tools (GitHub README rendering, Logseq, Foam) honor HTML img too. The narrow case where this matters: exporting to a markdown-strict renderer like raw GitBook. Documented as a known trade-off; revisit if users complain.

## [1.0.12] — 2026-05-17

### Fixed — Obsidian "invalid properties" banner on Windows-created pages

YAML frontmatter was built as raw f-string interpolation: `title: "{title}"\nraw_path: "{raw_path}"`. On Windows, `raw_path` contains backslash separators (`raw\webpages\artifacts\foo.md`), and `\w`/`\a` aren't valid YAML escape sequences inside a double-quoted scalar — Obsidian banners "invalid properties" at the top of every newly-created Windows page. Two fixes in `bin/lib/wiki_page.py`'s `build_wiki_page`:

- **Normalize `raw_path` to forward slashes** via a new `_posix(p)` helper before writing. POSIX-style paths are valid on every OS Python supports; Obsidian handles them in wikilinks the same way as backslash paths.
- **Universal YAML escape helper** `_yaml_dq(v)` applied to every double-quoted scalar (`title`, `source_type`, `raw_path`, `url`, `summary`). Escapes both backslashes (`\\`) and double quotes (`\"`) before interpolation. Defensive — protects against any future value with embedded backslashes or quotes.

### Fixed — broken wikilink in chat after wiki creation on Windows

1.0.11 sanitized the on-disk filename (`Web: Foo.md` → `Web — Foo.md`) but the `page_name` field returned to the JS plugin was still the unsanitized form. The chat panel's `[[Web: Foo]]` wikilink therefore didn't resolve to the actual file (`Web — Foo.md`). All three `'page_name': clean_title` returns in `create_wiki_page` now use `_safe_filename(clean_title)` so the reported page name matches the on-disk file.

### Changed — X.com image size cleanup

Web Clipper grabs `pbs.twimg.com/...?name=large` URLs by default, which are full-resolution (~2000px wide) and take over the Obsidian viewport, hiding body text beneath. Two layers of fix:

- **New `_rewrite_twimg_images(body)` in `process_clip.py`** runs on every new capture: rewrites `name=large` / `name=4096x4096` / `name=orig` → `name=medium` (~1200px, good display quality at ~half the bandwidth), AND adds Obsidian's `|600` width constraint inside the image's alt text so renderers honor an explicit max width even if some twimg URL pattern slips past the URL rewrite.
- **New `scripts/rewrite-twimg-sizes.py`** runs the same logic retroactively over existing raw files. Idempotent. Run with `--dry-run` first to preview. Verified on the dev vault: 60 image refs across 9 X.com captures rewritten cleanly. Available to ship to end users via a future "kb migrate" command or for power users to clone-and-run.

## [1.0.11] — 2026-05-17

### Fixed — Windows: wiki page creation no longer fails with `OSError [WinError 87]`

Athena's naming convention uses `:` very liberally ("X: ...", "Web: ...", "GitHub: ...", "LinkedIn: ..."). On macOS HFS+/APFS and Linux ext4 this works fine. **On Windows NTFS, `:` is the alternate-data-stream separator — an INVALID filename character** that causes `os.replace()` to throw `OSError [WinError 87] The parameter is incorrect` at the atomic write step in `wiki_page.py:1631`. End result on Windows: raw file saved, no wiki page created, chat panel said "Page added to knowledge base." (lying). 1.0.10's bumped stderr log limit finally made the OSError visible.

Fix in `bin/lib/wiki_page.py`:

- **New `_safe_filename(name)` helper** that sanitizes per-platform: POSIX returns `name` unchanged; Windows substitutes `:` → ` —` (em-dash with leading space, reads naturally) and drops other NTFS-reserved chars (`< > | ? * "`, control chars). Also drops the Unicode replacement character (`�`, `U+FFFD`) on all platforms — it's never user-intent and usually indicates upstream encoding damage.
- **Applied at the two `wiki_path` construction sites** (lines 1570 and 1605 — the initial path and the disambiguated path used when a collision is detected).
- **Frontmatter `title:` keeps the original colon-form** — sanitization is filename-only. Obsidian's `[[stem|alias]]` wikilink form lets the rendered display preserve the colon-prefixed title while the on-disk filename uses the em-dash.

### Fixed — chat panel honest about synthesis failure

When `_runWikiPageBuilder` returned a result without a `pageName` (Python ran but errored), the chat used to print `**Captured:** <url>` + `Page added to knowledge base.` regardless. **Effectively a lie**: nothing was added. Now the no-pageName-but-Python-present case shows `**Raw saved:** <url>` + a directive to open the dev console for the `[athena] wiki_page.py stderr:` line carrying the actual error. The capture-only-mode message (Python truly absent) is unchanged.

### Known Windows limitations still open

- **LLM via claude.cmd hits cmd.exe metachar escape edge cases.** Console shows `[athena] LLM stderr (exit code 255): > was unexpected at this time.` Athena's prompt template contains the literal `(*?"<>|)` string in the naming-convention rule — the carets escape `<`, `>`, `|` individually but cmd.exe's parser still trips on multi-metachar sequences. Gryphon's `win-spawn.js` documents this limitation and recommends "pipe via stdin instead of argv on Windows." Real fix: switch `llmProcessContent` to send prompt via stdin rather than the `-p` flag. Tracked for 1.0.12.
- **Cross-platform vault sync** (macOS vault opened on Windows or vice versa) will produce two different filenames for the same source — `Web: Title.md` on macOS, `Web — Title.md` on Windows. A one-time migration script renaming `:` → ` —` everywhere is tracked for 1.1.

## [1.0.10] — 2026-05-17

### Diagnostic improvements driven by Windows synthesis testing

Each one fixes a "we can't see why this failed" gap that surfaced during 1.0.9 Windows full-synthesis tests:

- **Python wiki_page.py stderr log limit bumped 200 → 2000 chars.** Python tracebacks regularly exceed 200 chars, and the 200-char cap routinely truncated before the actual `<ErrorType>: <message>` tail — leaving the user with a file/line pointer (`File ... line 1886, in <module>`) and no error class. 2000 covers any reasonable single-frame traceback; multi-frame traces may still need the manual reproduction command (`<json> | python .obsidian/plugins/athena/bin/lib/wiki_page.py --stdin 2>&1`) to see fully.

- **LLM stderr now surfaces on non-zero exit.** When `claude.cmd` (or any provider) exited with `code: 255`, the console previously showed only `LLM done {code: 255, len: 0}` followed by `LLM parse error: Unexpected end of JSON input` — invisible failure with no actionable signal. Now any non-zero exit prints `[athena] LLM stderr (exit code N): <stderr>` so the user can see auth failures, missing API keys, model errors, etc. directly. Logged only on non-zero exits to avoid noise on the success path.

### Fixed — inbox/url-resolved.tsv ENOENT on fresh vaults

Same class as 1.0.5's url-new.txt fix. The capture-only fallback path at `plugin.js:1133` tries to `readFileSync` the TSV, catches the ENOENT, and logs `[athena] ingest: url-resolved update failed: ENOENT...` — once per failed ingest. Harmless (the catch swallows it) but constant noise. Onload now creates the file as empty if missing. Companion to the 1.0.4 onload dir setup.

## [1.0.9] — 2026-05-17

### Changed — full wiki synthesis now works on a vanilla Community Plugins install

Before 1.0.9, wiki synthesis required either (a) cloning the full Athena vault from `polleoai/athena` to get the `bin/lib/*.py` sources, or (b) running the maintainer's `./scripts/deploy-to-vm.sh --full-vault` to copy them over. Community Plugins / BRAT users got capture-only by default — capture worked but no wiki page was synthesized.

1.0.9 ships Athena's Python synthesis sources **inside the plugin bundle**. The end-user experience for full synthesis is now:

1. Install Athena via Community Plugins (or BRAT).
2. Install Python 3.10+ on your system.
3. `python -m pip install pydantic` (the only non-stdlib Python dep).
4. Restart Obsidian — `kb add` runs end-to-end synthesis.

What changed in detail:

- **`build.js`** now copies `bin/lib/` (31 Python modules) + `bin/config/athena.default.json` into `.obsidian/plugins/athena/bin/` at build time. Plugin install size grows from ~1.3 MB to ~2.0 MB. `__pycache__` directories are excluded (machine-specific noise).
- **`plugin.js`** introduces a `resolvePythonScript(plugin, relPath)` helper that looks for the script in the plugin's install dir first, falls back to the vault root (for dev vaults and pre-1.0.9 `--full-vault` deploys). All four Python spawn sites now use it: `_runWikiPageBuilder` (`wiki_page.py`), the canonical-slug derivation (`slug.py`), and both Web Clipper paths (`process_clip.py` — handler + watchdog). The no-backend detection check at the chat result handler uses the same resolver, so the "synthesis was skipped" message only fires when Python truly can't be invoked.
- **README operating-modes section rewritten**: capture-only is no longer the default "Community Plugins user experience" — it's a fallback for users without Python. The recommended path is plugin + Python + one pip install. The "clone the full Athena vault" path stays documented for power users who want the terminal `kb` command set and the lint suite.

### Compatibility

- **No JS API change**, no Gryphon bump. The plugin still composes `GryphonChatView` and uses `wrapForCmdShim` (1.0.8) / `pythonCmd()` (1.0.8) under the hood.
- **Dev vaults work identically** — the plugin-dir bundled scripts are byte-for-byte copies of the vault-side `bin/lib/`, and the resolver picks plugin-dir first; falling back to vault-side is harmless.
- **Capture-only mode still exists** — users without Python get the same honest message as before, just with a clearer prescription ("install Python + pip install pydantic" instead of "clone the full Athena vault").
- **Plugin bundle size**: ~1.3 MB → ~2.0 MB. Well within Obsidian's reasonable plugin size norms.

## [1.0.8] — 2026-05-17

### Fixed — Windows: `kb add` no longer fails with "spawn EINVAL"

User-reported on a Windows VM: chat shows "Reading and summarizing..." then errors out with `KB command error: spawn EINVAL`. Root cause: Node 20+ on Windows refuses to `spawn()` `.cmd` / `.bat` files directly (CVE-2024-27980 mitigation) — `spawn(claudePath, ...)` returns EINVAL when `claudePath` is `claude.cmd` (the npm install shape on Windows). Two fixes:

- **`llmProcessContent` spawns through `wrapForCmdShim` on Windows .cmd binaries.** Reuses the existing Gryphon helper at `vendor/gryphon/packages/protect/src/win-spawn.js` — handles CommandLineToArgvW arg quoting, cmd.exe metachar escaping, and sets `windowsVerbatimArguments: true` (the Node 20+ requirement for spawning .cmd shims). No-op on POSIX. No new dependency.
- **Python invocations now use `pythonCmd()` instead of literal `"python3"`.** On Windows the default Python install ships `python.exe` — `python3.exe` only exists if the user explicitly installed it that way. Calling `spawn("python3", ...)` therefore returns ENOENT on most Windows setups. The new helper resolves to `"python"` on Windows and `"python3"` elsewhere. Affects `_runWikiPageBuilder` + the three `execFileSync` sites in the ingest, Web Clipper, and watchdog paths. Cached per-process.

### Known Windows limitations (still open)

- **`bin/kb` (bash script) is not runnable on Windows.** Affects the shell-capture fallback inside `runMechanical` and any `kb <verb>` not handled by the chat-side JS path. Workaround: capture via the Web Clipper drop or the chat `kb add` path (both go through `process_clip.py` / `llmProcessContent`, not the bash script). Full Windows shell-fallback parity needs a `bin/kb.cmd` Windows-native entry point — tracked for a future release.
- **Long prompts may exceed Windows' 8191-char command-line limit.** `llmProcessContent` passes the prompt via the `-p` argument. The raw-content prefix is capped at 4000 chars but the surrounding boilerplate brings the total close to the limit. Moving the prompt to stdin would solve this — also tracked for a future release.

## [1.0.7] — 2026-05-17

### Added — two audit dashboards: "did my clip go through?"

User-reported scenario: drop a clip via Web Clipper, refresh the URL Tracker, see only one of two recent additions. No way to tell whether the missing one was deduped, failed, still pending, or never arrived. Both new dashboards regenerate automatically on every ingest (Python backend / full-synthesis mode only; Community Plugins capture-only mode gets them in 1.1).

- **`inbox/URL Tracker.md` — All URLs section** (replaces the previous "Recently Added (last 20)" cap). Shows every entry in `inbox/url-resolved.tsv` newest-first with a status icon (`✓` captured, `↻` duplicate, `✗` failed/thin/uncapturable, `⊘` removed), the capture timestamp, and either a wikilink (success) or strikethrough title plus URL (failure). Full audit history of every URL the user submitted, ever.
- **`wiki/dashboards/All Pages.md` — new chronological page index**. Scans every wiki page, groups by `date_added` frontmatter (falls back to `last_updated` / `date` / `created`; missing→"Undated" bucket pinned to bottom). Within each date group: alphabetical, with the page summary inlined for context. Companion to URL Tracker: that one shows inputs, this shows outputs. "What did I do today?" is now one click instead of a grep.

### Fixed — TSV row-shape disambiguation in dashboard generator

`inbox/url-resolved.tsv` has two row shapes from across the project's history: 4-col modern (`status \t title \t url \t ISO-timestamp`) and 5-col legacy (`status \t title \t source_url \t resolved_url \t type`). The old `generate_url_tracker` parsed `parts[3]` unconditionally as a date, silently routing the legacy `resolved_url` field into the date column. Invisible before 1.0.7 (the old dashboard didn't display dates); surfaced immediately when the new "All URLs" section started rendering timestamps. Fix: ISO-date regex on `parts[3]` decides between date vs resolved_url; legacy rows get an empty date string (acceptable — they predate timestamp tracking).

## [1.0.6] — 2026-05-17

### Fixed

- **Up-arrow now recalls `kb add` and other mechanical prompts.** Gryphon's prompt history walk filters out any message with `source !== "llm"` (`vendor/gryphon/.../chat-view.js:3143`) — the design assumes consuming plugins log "domain-specific commands" they don't want users to re-navigate to. Athena tags `kb` commands as `source: "mechanical"` because they bypass the LLM, but they ARE user prompts and should be recallable. `AthenaChatView._getPromptHistory()` (`src/athena/athena-chat-view.js`) now overrides the walk by shallow-relabeling `mechanical` → `llm` for super's call only. The underlying message objects in `this.messages` and `_fullHistory` keep their real `mechanical` source so other Gryphon filters (context inclusion, retention policy, last-user-message lookup) still route correctly.

### Changed — honest UX for fresh-vault installs

- **Chat now tells the user what actually happened when wiki synthesis is skipped.** On Community-Plugins / BRAT / fresh-vault installs, the plugin saves the raw file but `_runWikiPageBuilder` silently fails because `bin/lib/wiki_page.py` doesn't exist. The chat used to say "Captured: <url> / Page added to knowledge base." — misleading, since no wiki page was actually created. Now: `**Raw saved:** <url>` plus an explicit "Wiki synthesis was skipped — Athena's Python backend isn't installed in this vault. Capture-only mode is what Community Plugins users currently get. For full wiki synthesis, clone the Athena vault from https://github.com/polleoai/athena and open it in Obsidian instead."

### Added — README + scripts for two-mode testing

- **README documents capture-only vs. full synthesis modes** with a table explaining what each provides and how to switch between them. Sets honest expectations for Community Plugins users (who get capture-only) and points at the polleoai/athena repo as the path to full synthesis. The roadmap to bring synthesis into the JS plugin is tracked as the 1.1 milestone.
- **`scripts/install-to-vault.sh --full-vault`** copies `bin/` + `config/` into the target vault in addition to the plugin bundle. Without the flag the script installs in capture-only mode (mirrors the Community Plugins experience). With the flag the vault gets the full Python backend so synthesis runs.
- **`scripts/deploy-to-vm.sh --full-vault`** same idea for the HTTP-served VM deploy. The tarball is restructured to be vault-root-anchored (was `plugins/`-anchored) so plugin dirs and `bin/`/`config/` can share one archive without per-mode tar roots. Linux and Windows one-liners updated to extract from the vault root instead of `.obsidian/`. Backward-incompatible for anyone who copy-pasted a 1.0.5 one-liner; re-paste the new printed instructions.

## [1.0.5] — 2026-05-17

### Fixed

- **`inbox/url-new.txt` watcher no longer logs an ENOENT warning on fresh vaults.** `_setupUrlNewWatcher` called `fs.watch(urlNewPath, ...)` directly, but `fs.watch` throws ENOENT when the file doesn't exist — and on Community-Plugins / BRAT / fresh-vault installs it never does. Console showed `[athena] Could not watch url-new.txt: ENOENT: no such file or directory, watch '/<vault>/inbox/url-new.txt'`, and the watcher never attached — so URLs later added via "URL Tracker.md" / "Add URLs.md" sync would never trigger ingest. Fix: create the file as empty (`fs.writeFileSync(urlNewPath, "")`) before attaching the watcher. Idempotent — no-ops on dev vaults that have the file already. Same class as 1.0.4's fresh-vault ENOENT fix.

## [1.0.4] — 2026-05-17

### Fixed

- **`kb add <url>` from chat no longer throws ENOENT on fresh vaults.** Athena's three-layer dir structure (`raw/{webpages,papers,repos,videos}/artifacts/`, `inbox/`) is created on plugin load by the Python backend in dev vaults, but Community-Plugins / BRAT / brand-new-vault installs don't have a backend installed and therefore lack the dirs entirely. Browser capture would succeed in extracting page text, then `fs.writeFileSync(rawFilePath, ...)` would throw `ENOENT: no such file or directory, open '/<vault>/raw/webpages/artifacts/<slug>.md'` because the parent dir was missing. Fix is two layers:
  - **Onload structure setup** in `plugin.js` creates the five standard Athena dirs if missing (idempotent — no-ops on dev vaults that already have them). User now sees the Athena layout in the vault file tree immediately after enabling the plugin, even before the first capture.
  - **Per-write defensive mkdir** before both `writeFileSync` calls in the ingest path (browser-capture write at line ~1001 and content-paste write at line ~1058). Belt-and-suspenders against any future category dir we forgot to seed in onload.

### Known issues

- **Up-arrow doesn't recall the previous message after Obsidian restart.** Reported on Linux. Gryphon's prompt history mechanism (`_getPromptHistory` in `chat-view.js`) reads from persisted `chat-history.json` plus the live message list, so this *should* work in principle. Two plausible failure modes under investigation: (a) persistence wasn't flushed before the restart (likely if the prior `kb add` hung), (b) `kb add`-issued messages have role/type `mechanical` and may be filtered out of the history walk. Will be addressed in a follow-up release after we confirm which is firing — pending dev-console logs from a reproducible Linux session.

## [1.0.3] — 2026-05-17

### Fixed

- **`kb add <url>` from chat no longer hangs on Linux.** Athena's `_browserWindowCapture` (`src/athena/plugin.js`) wrapped `await win.loadURL(url)` and a nested 6s `setTimeout` inside a single try/catch with no outer timeout. On Linux — especially Ubuntu 22.04+ with Snap-installed Obsidian under AppArmor — Chromium's sandbox helper can fail to start the renderer, leaving `loadURL` unfulfilled forever. Because the 6s wait was nested *inside* the await chain, it never fired, the catch never ran, and the user saw "Capturing URL..." indefinitely with no fallback to `_webviewCapture` or the shell path. Fix: wrap the full BrowserWindow load+extract chain in a `Promise.race` against a 15s outer timeout (mirrors `_webviewCapture`'s existing 15s budget) and ensure `win.close()` runs on both success and failure paths via a `win.isDestroyed()` guard in the catch.
- **Sandbox policy unchanged** — Athena's own CLAUDE.md security rule "Browser capture must run with sandbox enabled" stands. The Linux hang is now a graceful 15s fall-through, not a policy loosening.

### Changed

- **Capture status line surfaces fallback transitions.** Previously the chat status said "Capturing URL..." for the full duration (up to ~30s across BrowserWindow + webview + shell retries) with no feedback when a stage failed. Now: "Capturing URL..." → "Browser capture failed, trying webview..." → "Webview capture failed, trying Python backend..." → result. The `browserCapture(url, updateStatus)` signature gained an optional status callback that the ingest caller wires through.
- **Final-failure message is informative.** When all three capture paths fail (BrowserWindow + webview + bin/kb shell), the chat used to say only "Capture failed. Check the URL and try again." — useless on a fresh Linux VM where the user has no idea what to retry. New message: "Browser capture failed (this can happen on Linux with sandbox restrictions, or with pages that block automation like Cloudflare-protected sites). Try the Obsidian Web Clipper extension instead."

## [1.0.2] — 2026-05-16

### Fixed

- **Chat panel welcome no longer says "Welcome to Gryphon".** The panel is rendered by the bundled Gryphon chat view (composition), and Gryphon's `displayText` option only controlled the workspace tab label — the welcome panel itself hardcoded its own brand string. Athena now uses a thin `AthenaChatView` subclass (`src/athena/athena-chat-view.js`) that lets Gryphon render the functional welcome (provider cards, dismiss, lifecycle) and then swaps the brand-bearing text nodes via stable DOM selectors. Heading is "Welcome to Athena — your Second Brain"; body emphasizes ingest + structured KB. Security disclosure retained (the protection still fires inside the bundled view); the "Tune the rules in Settings → Gryphon → Security" actionable was dropped since Athena doesn't yet expose that UI in its own settings tab. The fix lives entirely in Athena — no Gryphon change required, so the dependency direction (Athena → Gryphon) stays correct.

## [1.0.1] — 2026-05-16

### Changed

- **Bundled Gryphon submodule pinned to 1.6.1** (was 1.6.0). Auto-bump-driven release; no Athena-side code changes. Gryphon 1.6.1 ships a README rewrite for multi-provider support. End-user behavior unchanged.

## [1.0.0] — 2026-05-16

First public release. Athena is your Second Brain in Obsidian: ingest URLs, papers, repos, videos, and screenshots into a three-layer knowledge base (raw sources / LLM-maintained wiki / schema), search and synthesize across them, and chat with the model of your choice through the bundled Gryphon plugin.

This release closes out Obsidian Community Plugins compliance and bundles vendored Gryphon 1.6.0 (whose v1.6.0 itself completed Community Plugins compliance for the chat surface).

### Added — Community Plugins compliance

- **README.md** with feature overview, install instructions, and full network endpoint + system identity disclosures for every bundled SDK.
- **CHANGELOG.md** in Keep-a-Changelog format with semver versioning.
- **versions.json** mapping each release to its minimum Obsidian app version.
- **`.github/workflows/release.yml`** — tag-push and `workflow_dispatch` triggered CI release pipeline. Builds the bundle from a clean checkout, verifies the tag matches `manifest.version`, attaches sigstore build-provenance attestations to every release asset (`main.js`, `manifest.json`, `styles.css`, install zip), and creates the GitHub Release from the matching CHANGELOG section. Tag shape is validated against strict semver before any build runs.
- **`scripts/release-smoke-test.sh`** — runs `npm install + npm run build:all` from a fresh clone, verifies the three required output artifacts, and (when pnpm is available) repeats the run under pnpm. Gates the release workflow against Obsidian's pnpm-based scorecard sandbox.
- **manifest.json** now includes `authorUrl: https://www.polleo.ai`. `author` is `POLLEO.AI`, aligning with the Gryphon manifest.

### Changed — bundled Gryphon submodule pinned to 1.6.0

- Brings in Gryphon's own Community Plugins compliance work (CSS hygiene — no `!important`, no `:has()`, no duplicate selectors; README placeholder hunt; sigstore-attested release pipeline; pnpm-friendly build via `pnpm-workspace.yaml` + `file:` workspace deps). Athena bundles Gryphon's stylesheet bit-for-bit, so all of those fixes flow through to Athena's bundle.
- **No behavior change for end users.** Same UI, same chat surface, same `kb` command set, same wiki layout.

### Added — internal safety net

- `bin/lib/wiki_page.py`: `'linkedin 404 — page not found'`, `'404 — page not found'`, and `'page not found'` added to `GENERIC_TITLES`. Symmetric to `UI_NOISE_RE`'s body-side 404 filter — captures during a transient 404 no longer leave the chrome title as the wiki page name once the raw is corrected.
- `kb lint` Section 30c: auto-trash raw files with no YAML frontmatter at all. Catches the content-in-wrong-file class (e.g. a GitHub README clipped under a `linkedin-com-posts-ugcpost-*` slug). Moves to `.kb-trash/<ts>_no-frontmatter-lint/` for forensic recovery via `kb undo`. The raw is removed from `raw_files` so subsequent lint sections don't fail on the missing file.

### Compatibility

- **Minimum Obsidian app version**: 1.0.0.
- **Desktop only.** Athena's terminal `kb` command set and Python KB engine assume a desktop filesystem with Node child-process spawning; mobile is not supported.
- **No new code dependencies** added in this release vs. the 0.12.x line. Bundle composition is `vendor/gryphon` (Anthropic, OpenAI, Google SDKs) plus Athena's own plugin shell (no SDKs of its own).
