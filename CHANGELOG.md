# Athena — Changelog

All notable changes to the Athena Obsidian plugin are documented here. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versioning is [SemVer](https://semver.org/).

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
