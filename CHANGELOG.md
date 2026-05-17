# Athena — Changelog

All notable changes to the Athena Obsidian plugin are documented here. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versioning is [SemVer](https://semver.org/).

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
