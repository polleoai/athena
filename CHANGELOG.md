# Athena — Changelog

All notable changes to the Athena Obsidian plugin are documented here. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versioning is [SemVer](https://semver.org/).

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
