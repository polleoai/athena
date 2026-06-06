"""unified_ingest — single entry point for all content ingestion.

Replaces three divergent paths that each made independent routing decisions
and routinely disagreed about what a URL "is":

  1. server/athena/server.py:_process_clip   (Web Clipper drops via MCP)
  2. bin/lib/process_clip.py:process_clip    (CLI clip processor watching
                                              inbox/Clippings/)
  3. bin/kb-capture                           (bash per-type branches for
                                              kb add <url>)

The recurring bug class — arXiv URLs via Web Clipper landing in raw/webpages/
instead of raw/papers/, GitHub readmes mis-classified as "webpage", same
URL producing different raws depending on entry point — is the symptom of
three independent routing decisions. Fix: one routing oracle (url_detect),
one dispatch table, one handler per source_type. Everywhere converges here.

Contract:

  caller → IngestInput {vault_root, url, title, body|html, source_hint?, ...}
  ingest(input)
    1. canonicalize URL
    2. determine source_type via url_detect (the OAT, "one and only truth")
    3. dispatch to _handle_<source_type>
    4. handler returns IngestResult {raw_path, source_type, ...}

Per-source-type handlers live in this module. For source_types whose
extraction logic still lives in `bin/kb-capture` (paper, repo, video, doc,
etc.), the handler shells out to `bin/kb-capture <url>` and returns the
written raw path. This preserves the existing bash extractors (which are
working and well-tested) while still consolidating the ROUTING decision
here — which is the actual bug surface. Future migrations can port each
handler to native Python without touching callers.

This module is the only place that maps URL → source_type. Callers MUST
NOT do their own routing; they MUST NOT consult source_kind() or
_KIND_TO_CATEGORY directly. If a caller needs to know what bucket a URL
will land in, call `route(url)` here and read the result.

Pure dependency direction:
  unified_ingest  →  url_detect, url_canonical, raw_writer, arcus_html
  (nothing imports unified_ingest from inside those modules)
"""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Lazy module imports happen inside handlers; this module runs in many
# contexts (MCP stdio server, CLI, tests, watcher), and a top-level import
# of every dependency would slow cold-starts that may not need all paths.


# ─────────────────────────────────────────────────────────────────────
# Public types
# ─────────────────────────────────────────────────────────────────────


class UnifiedIngestError(Exception):
    """Raised when ingestion cannot complete. Callers should catch this
    at process boundaries and surface a clear error to the user."""


@dataclass
class IngestInput:
    """Everything the caller can tell us about a content item.

    The contract:
      - `url` is REQUIRED. Routing depends on it.
      - At least one of `body`, `html`, `pdf_bytes` should be provided,
        OR `fetch_if_missing=True` (the default) so the handler can fetch.
      - `source_hint` is advisory only — url_detect overrides it for
        URLs that match a known platform pattern (arxiv, github, youtube).
        The hint is honored when url_detect returns the catch-all
        "webpage" type AND the hint is more specific.
      - `clipped_via` flows into the raw's frontmatter as provenance —
        important for distinguishing Web Clipper drops, kb-capture runs,
        deep-capture re-fetches, and tests.
    """

    vault_root: Path
    url: str
    title: str = ""
    body: str = ""           # markdown body, if pre-converted
    html: str = ""           # raw HTML, if available
    pdf_bytes: bytes = b""   # PDF bytes, if pre-fetched
    source_hint: str = ""    # caller's guess at source_type
    clipped_via: str = ""    # provenance tag
    extra: dict[str, Any] = field(default_factory=dict)
    fetch_if_missing: bool = True
    deep: bool = False       # use Playwright (deep mode) for JS-rendered

    def __post_init__(self) -> None:
        if not self.url or not self.url.strip():
            raise UnifiedIngestError("IngestInput.url is required")
        if isinstance(self.vault_root, str):
            self.vault_root = Path(self.vault_root)
        if not self.vault_root.is_dir():
            raise UnifiedIngestError(
                f"vault_root must be an existing directory: {self.vault_root}"
            )


@dataclass
class IngestResult:
    """What ingest() returns to the caller.

    `raw_path` is the authoritative artifact location. `was_re_routed`
    is True when url_detect disagreed with the caller's source_hint —
    callers logging the result should surface this since it usually
    indicates a clip that was about to be misfiled.
    """

    raw_path: Path
    source_type: str
    canonical_url: str
    title: str
    extracted_via: str
    was_re_routed: bool
    related_urls_queued: list[str] = field(default_factory=list)


# ─────────────────────────────────────────────────────────────────────
# Routing
# ─────────────────────────────────────────────────────────────────────


def route(url: str, source_hint: str = "") -> dict[str, Any]:
    """Determine the source_type for a URL. Single source of truth.

    Returns the dict shape from url_detect.detect_type plus a
    `was_re_routed` boolean indicating whether the result diverged from
    `source_hint`. Callers MUST use this rather than calling
    url_detect.detect_type directly — `route()` is the integration point
    where future routing nuances (per-host overrides, user-rule
    customization, A/B tests) will land.
    """
    from url_detect import detect_type

    detection = detect_type(url)
    detected = detection.get("source_type", "webpage")

    # Honor source_hint only when it's more specific than the catch-all.
    # We trust url_detect's platform-pattern matches absolutely (arxiv,
    # github, youtube are unambiguous from the URL alone); but when
    # url_detect falls back to "webpage" (the default), a caller-provided
    # hint may know something we don't (the HEAD-request fallback may
    # have missed because of a redirect, server quirk, etc.).
    final_type = detected
    if detected == "webpage" and source_hint and source_hint != "webpage":
        # Only allow the hint to OVERRIDE the default; never let it
        # downgrade a specific detection to webpage.
        if source_hint in _VALID_SOURCE_TYPES:
            final_type = source_hint

    was_re_routed = bool(source_hint) and source_hint != final_type

    return {
        **detection,
        "source_type": final_type,
        "was_re_routed": was_re_routed,
    }


_VALID_SOURCE_TYPES = frozenset({
    "paper", "repo", "webpage", "video", "image",
    "doc", "spreadsheet", "slides", "book",
})


# ─────────────────────────────────────────────────────────────────────
# Public entry point
# ─────────────────────────────────────────────────────────────────────


def ingest(input: IngestInput) -> IngestResult:
    """Single entry point for all content ingestion.

    Routes the URL via `route()`, dispatches to the appropriate handler,
    returns IngestResult. All other ingest paths in athena should
    eventually funnel through here.
    """
    # 1. Canonicalize the URL once, here. Handlers receive the canonical
    #    form — no handler should canonicalize separately.
    from url_canonical import canonicalize

    canonical_url = canonicalize(input.url).url

    # 2. Route
    routing = route(canonical_url, input.source_hint)
    source_type = routing["source_type"]

    # 3. Dispatch
    handler = _HANDLERS.get(source_type)
    if handler is None:
        # Defensive: should never happen given _VALID_SOURCE_TYPES, but
        # if a new source_type appears in url_detect without a handler
        # here we surface it loudly rather than silently misfiling.
        raise UnifiedIngestError(
            f"no handler registered for source_type={source_type!r}; "
            f"url={canonical_url!r}. Add a handler to _HANDLERS in "
            f"unified_ingest.py."
        )

    result = handler(input, routing, canonical_url)
    return result


# ─────────────────────────────────────────────────────────────────────
# Handlers
# ─────────────────────────────────────────────────────────────────────


def _handle_webpage(
    input: IngestInput,
    routing: dict[str, Any],
    canonical_url: str,
) -> IngestResult:
    """Default handler for generic HTML pages.

    Three input modes — pick the cheapest path that produces a usable body:
      A. `input.body` provided (Web Clipper today) → pass through with
         chrome stripping + title derivation (preserves existing process_clip
         behavior).
      B. `input.html` provided (future Web Clipper HTML template, or any
         pre-fetched HTML) → run arcus's HTML-from-string extractor.
      C. Neither → fetch via arcus_html.ingest_html_url (the kb add <url>
         existing path).
    """
    if input.body and input.body.strip():
        return _handle_webpage_from_markdown_body(input, routing, canonical_url)

    if input.html and input.html.strip():
        return _handle_webpage_from_html(input, routing, canonical_url)

    if input.fetch_if_missing:
        return _handle_webpage_by_fetch(input, routing, canonical_url)

    raise UnifiedIngestError(
        f"webpage handler has no body/html and fetch_if_missing=False; "
        f"cannot ingest {canonical_url}"
    )


def _handle_webpage_from_markdown_body(
    input: IngestInput,
    routing: dict[str, Any],
    canonical_url: str,
) -> IngestResult:
    """Path A: caller provided pre-converted markdown (today's Web Clipper).

    Delegates to the existing chrome-strip + title-fallback + image-rewrite
    pipeline that lived in process_clip.process_clip(). That logic is sound;
    we just want the routing decision to land here instead of there.

    Specifically: this function does NOT re-derive the source_type. We're
    here precisely because route() said this URL is a webpage; if the
    caller's body was a poisoned arxiv-disguised-as-webpage, we'd never
    reach this function (route() would have sent us to _handle_paper).
    """
    from raw_writer import write_raw
    from process_clip import (
        _is_bait_title, _derive_title_from_body, _best_title_from_url_slug,
        _strip_linkedin_chrome, _rewrite_twimg_images, _strip_blob_videos,
        _queue_referenced_urls,
    )
    import re as _re

    body = input.body
    title = input.title or ""

    # LinkedIn chrome stripping — keep behavior identical to process_clip.
    if "linkedin.com" in canonical_url:
        body = _strip_linkedin_chrome(body)
        if not body.strip():
            raise UnifiedIngestError(
                f"webpage body empty after LinkedIn chrome strip for "
                f"{canonical_url} — no `## Feed post` marker found, or "
                f"post body is empty between feed marker and reactions section"
            )

    # Bait-title fallback (e.g., "Post | LinkedIn", "Tweet | X").
    if _is_bait_title(title):
        derived = _derive_title_from_body(body)
        if derived:
            title = derived
    # Masthead-title fallback: site/org name extracted as title while the
    # article headline sits in a later H1 (e.g. rdi.berkeley.edu blog).
    # Only fires when the title looked legitimate (not bait) but a body H1
    # matches the URL slug better. See _best_title_from_url_slug.
    else:
        article_title = _best_title_from_url_slug(title, body, canonical_url)
        if article_title:
            title = article_title

    # Image + video URL cleanup. No-op on bodies without those patterns.
    body = _rewrite_twimg_images(body)
    body = _strip_blob_videos(body, source_url=canonical_url)

    # Strip trailing ellipsis from truncated URLs.
    body = _re.sub(r'(https?://[^\s]*?)…', r'\1', body)

    # Queue referenced github + arxiv URLs (auto-discovery).
    related_urls: list[str] = []
    try:
        related_urls = _queue_referenced_urls(body, input.vault_root) or []
    except Exception as exc:  # noqa: BLE001 — auto-queue is best-effort
        print(
            f"[unified_ingest] url-queue helper failed: {exc}",
            file=sys.stderr,
        )

    if not title.strip():
        title = canonical_url  # last-resort; raw_writer will reject empty

    extra = {
        "clipped_via": input.clipped_via or "web-clipper",
        **input.extra,
    }

    try:
        raw_path = write_raw(
            vault_root=input.vault_root,
            source_type="webpage",
            url=canonical_url,
            title=title,
            body=body,
            extra=extra,
            canonicalize_url=False,  # already canonicalized in ingest()
        )
    except Exception as exc:  # raw_writer surfaces RawWriterError + degraded
        raise UnifiedIngestError(
            f"webpage write failed for {canonical_url}: {exc}"
        ) from exc

    return IngestResult(
        raw_path=raw_path,
        source_type="webpage",
        canonical_url=canonical_url,
        title=title,
        extracted_via="passthrough",
        was_re_routed=routing.get("was_re_routed", False),
        related_urls_queued=related_urls,
    )


def _handle_webpage_from_html(
    input: IngestInput,
    routing: dict[str, Any],
    canonical_url: str,
) -> IngestResult:
    """Path B: caller provided raw HTML — convert to markdown via arcus
    using the SAME pipeline that kb add <url> uses for direct fetches.

    Not yet implemented in this turn — placeholder until the HTML-capture
    Web Clipper template lands (see ~/.claude/plans for the rollout).
    For now, falls back to the fetch path; the HTML is discarded.
    """
    # TODO(unified_ingest): when arcus exposes an extract-from-html-string
    # entry point, wire it here so the Web Clipper HTML template avoids the
    # network round-trip. For now, refetch.
    return _handle_webpage_by_fetch(input, routing, canonical_url)


def _handle_webpage_by_fetch(
    input: IngestInput,
    routing: dict[str, Any],
    canonical_url: str,
) -> IngestResult:
    """Path C: no body, no HTML — let arcus fetch and extract.

    Wraps the existing `arcus_html.ingest_html_url` which is the same
    function `bin/kb-capture` calls today for webpage URLs. One extractor,
    one converter, identical output regardless of entry point.
    """
    from arcus_html import ArcusHtmlError, ingest_html_url
    import time as _time

    date_str = _time.strftime("%Y-%m-%d")

    try:
        raw_path = ingest_html_url(
            vault=input.vault_root,
            url=canonical_url,
            date_str=date_str,
            source_type="webpage",
            deep=input.deep,
        )
    except ArcusHtmlError as exc:
        raise UnifiedIngestError(
            f"arcus webpage fetch failed for {canonical_url}: {exc}"
        ) from exc

    # ingest_html_url chose its own title; recover it from the file for
    # the IngestResult.
    try:
        title = _read_title_from_raw(raw_path)
    except OSError:
        title = canonical_url

    return IngestResult(
        raw_path=raw_path,
        source_type="webpage",
        canonical_url=canonical_url,
        title=title,
        extracted_via="arcus_html",
        was_re_routed=routing.get("was_re_routed", False),
    )


def _handle_paper(
    input: IngestInput,
    routing: dict[str, Any],
    canonical_url: str,
) -> IngestResult:
    """Paper handler: arxiv URLs, direct PDF URLs, Google Drive PDF viewers.

    Paper extraction (arxiv abstract scrape, PDF download + magic-byte
    validation, arcus_file extraction) currently lives in bin/kb-capture's
    `paper)` case branch (~line 1001+). Porting that to Python is in-scope
    for a future migration but not for this turn — instead we shell out to
    `bin/kb-capture <url>` which is idempotent and dispatches correctly.

    This handler's job in this turn: ensure that paper URLs arriving via
    ANY entry point (Web Clipper, MCP kb_add_content, CLI process_clip,
    direct kb add) all land in raw/papers/ — which they do because route()
    has already classified the URL as `paper` before reaching us. The
    handler itself just orchestrates the extraction.
    """
    return _delegate_to_kb_capture(
        input, routing, canonical_url, source_type="paper",
        extracted_via="kb_capture (paper branch)",
    )


def _handle_repo(
    input: IngestInput,
    routing: dict[str, Any],
    canonical_url: str,
) -> IngestResult:
    """Repo handler: github URLs. Delegates to kb-capture's `repo)` branch
    which uses `gh api` for README + metadata extraction."""
    return _delegate_to_kb_capture(
        input, routing, canonical_url, source_type="repo",
        extracted_via="kb_capture (repo branch)",
    )


def _handle_video(
    input: IngestInput,
    routing: dict[str, Any],
    canonical_url: str,
) -> IngestResult:
    """Video handler: youtube URLs. Delegates to kb-capture's `video)`
    branch which calls arcus_video / yt-dlp for transcript + metadata."""
    return _delegate_to_kb_capture(
        input, routing, canonical_url, source_type="video",
        extracted_via="kb_capture (video branch)",
    )


def _handle_doc(input: IngestInput, routing: dict[str, Any], canonical_url: str) -> IngestResult:
    """DOCX/ODT documents. Delegates to kb-capture (which uses arcus_file)."""
    return _delegate_to_kb_capture(
        input, routing, canonical_url, source_type="doc",
        extracted_via="kb_capture (doc branch)",
    )


def _handle_spreadsheet(input: IngestInput, routing: dict[str, Any], canonical_url: str) -> IngestResult:
    """XLSX spreadsheets. Delegates to kb-capture (arcus_file)."""
    return _delegate_to_kb_capture(
        input, routing, canonical_url, source_type="spreadsheet",
        extracted_via="kb_capture (spreadsheet branch)",
    )


def _handle_slides(input: IngestInput, routing: dict[str, Any], canonical_url: str) -> IngestResult:
    """PPTX presentations. Delegates to kb-capture (arcus_file)."""
    return _delegate_to_kb_capture(
        input, routing, canonical_url, source_type="slides",
        extracted_via="kb_capture (slides branch)",
    )


def _handle_book(input: IngestInput, routing: dict[str, Any], canonical_url: str) -> IngestResult:
    """EPUB books. Delegates to kb-capture (arcus_file)."""
    return _delegate_to_kb_capture(
        input, routing, canonical_url, source_type="book",
        extracted_via="kb_capture (book branch)",
    )


def _handle_image(input: IngestInput, routing: dict[str, Any], canonical_url: str) -> IngestResult:
    """Image files (png/jpg/gif/webp). Delegates to kb-capture."""
    return _delegate_to_kb_capture(
        input, routing, canonical_url, source_type="image",
        extracted_via="kb_capture (image branch)",
    )


# ─────────────────────────────────────────────────────────────────────
# Dispatch table
# ─────────────────────────────────────────────────────────────────────

_HANDLERS = {
    "webpage": _handle_webpage,
    "paper": _handle_paper,
    "repo": _handle_repo,
    "video": _handle_video,
    "doc": _handle_doc,
    "spreadsheet": _handle_spreadsheet,
    "slides": _handle_slides,
    "book": _handle_book,
    "image": _handle_image,
}


# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────


def _delegate_to_kb_capture(
    input: IngestInput,
    routing: dict[str, Any],
    canonical_url: str,
    *,
    source_type: str,
    extracted_via: str,
) -> IngestResult:
    """Shell out to bin/kb-capture for source_types whose extraction logic
    still lives in bash. The kb-capture script does its own routing
    (intentionally — we want it to keep working when called directly via
    `kb add <url>`), so when we delegate to it, it re-dispatches to the
    correct per-type branch. The doubled routing is a wart we'll remove
    when those handlers move into this module; for now it's harmless
    because both dispatches reach the same conclusion.
    """
    kb_capture = input.vault_root / "bin" / "kb-capture"
    if not kb_capture.is_file():
        raise UnifiedIngestError(
            f"bin/kb-capture not found at {kb_capture} — cannot delegate "
            f"{source_type} extraction"
        )

    env = dict(os.environ)
    env["KB_ROOT"] = str(input.vault_root)

    try:
        proc = subprocess.run(
            # kb-capture is now cross-platform Python — invoke via the
            # interpreter so it runs on native Windows (no shebang there).
            [sys.executable, str(kb_capture), canonical_url],
            cwd=str(input.vault_root),
            env=env,
            capture_output=True,
            text=True,
            timeout=600,  # arxiv PDF downloads can be slow
        )
    except subprocess.TimeoutExpired as exc:
        raise UnifiedIngestError(
            f"kb-capture timed out for {canonical_url} (source_type="
            f"{source_type}); 600s limit exceeded"
        ) from exc

    if proc.returncode != 0:
        raise UnifiedIngestError(
            f"kb-capture failed for {canonical_url} (exit "
            f"{proc.returncode}): {proc.stderr.strip() or proc.stdout.strip()}"
        )

    # kb-capture prints the written raw path on its last stdout line.
    # We need that path for IngestResult.raw_path.
    raw_path = _extract_raw_path_from_kb_capture_output(
        proc.stdout, input.vault_root, source_type, canonical_url,
    )

    try:
        title = _read_title_from_raw(raw_path)
    except OSError:
        title = canonical_url

    return IngestResult(
        raw_path=raw_path,
        source_type=source_type,
        canonical_url=canonical_url,
        title=title,
        extracted_via=extracted_via,
        was_re_routed=routing.get("was_re_routed", False),
    )


def _extract_raw_path_from_kb_capture_output(
    stdout: str,
    vault_root: Path,
    source_type: str,
    url: str,
) -> Path:
    """Parse kb-capture's stdout for the written raw path. kb-capture's
    bash logic prints lines like `[capture] wrote raw/papers/artifacts/
    arxiv-2605.11086.md` on success. We grep for the most recent such
    line and resolve against vault_root.

    Falls back to a guess based on source_type + URL slug if no explicit
    write-line is found — better to return a probably-wrong path than to
    fail the whole ingest when the raw is actually on disk.
    """
    import re as _re

    write_match = _re.findall(
        r'wrote\s+(raw/[^\s]+\.(?:md|pdf))', stdout
    )
    if write_match:
        return vault_root / write_match[-1]

    # Fallback: derive expected path from source_type + canonical URL.
    # This matches raw_writer's slug derivation roughly; if kb-capture
    # used a different slug we'll be wrong but kb_index will find the
    # actual file on its next run.
    from url_canonical import canonicalize
    from slug import derive_slug

    cat_dir = {
        "paper": "papers", "repo": "repos", "webpage": "webpages",
        "video": "videos", "image": "images", "book": "books",
        "doc": "docs", "spreadsheet": "spreadsheets", "slides": "slides",
    }.get(source_type, "webpages")

    try:
        slug = derive_slug(cat_dir, canonicalize(url).url, "")
    except Exception:  # noqa: BLE001 — best-effort fallback
        slug = "unknown"

    return vault_root / "raw" / cat_dir / "artifacts" / f"{slug}.md"


def _read_title_from_raw(raw_path: Path) -> str:
    """Extract the `title:` field from a raw file's frontmatter. Used so
    the IngestResult carries the post-write title (which may differ from
    the input title after fallback derivation)."""
    import re as _re

    text = raw_path.read_text(encoding="utf-8")
    match = _re.search(r'^title:\s*"?(.+?)"?\s*$', text, _re.MULTILINE)
    return match.group(1).strip() if match else ""


# ─────────────────────────────────────────────────────────────────────
# Convenience: from-clip-file entry point
# ─────────────────────────────────────────────────────────────────────


def ingest_clip_file(clip_path: Path, vault_root: Path) -> IngestResult:
    """Parse a Web Clipper drop file and route through ingest().

    This is the function process_clip.process_clip() should migrate to —
    same behavior (read clip, decide what to do, write raw) but routing
    is consolidated here.
    """
    from raw_parser import read_raw_frontmatter

    if not clip_path.is_file():
        raise UnifiedIngestError(f"clip not found: {clip_path}")

    fm, body = read_raw_frontmatter(clip_path)
    if not fm:
        raise UnifiedIngestError(
            f"clip has no parseable frontmatter: {clip_path.name}"
        )

    title = fm.get("title", "").strip() if isinstance(fm.get("title"), str) else ""
    url = fm.get("source", "").strip() if isinstance(fm.get("source"), str) else ""
    clipped_via = (fm.get("clipped_via") or "web-clipper").strip()

    if not url:
        raise UnifiedIngestError(
            f"clip has no source URL: {clip_path.name} — Web Clipper output "
            f"should include `source:` in frontmatter; check the extension "
            f"config"
        )

    if not title:
        title = clip_path.stem

    return ingest(IngestInput(
        vault_root=vault_root,
        url=url,
        title=title,
        body=body,
        clipped_via=clipped_via,
        fetch_if_missing=False,  # clip body is what we have; don't refetch
    ))
