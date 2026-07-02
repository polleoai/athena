"""arcus_html — HTML / X.com tweet ingest via arcus, wrapped into athena's
raw webpage artifact format.

Athena delegates URL fetching + HTML→markdown conversion + X.com tweet
extraction to arcus's HtmlProvider (which itself wraps the lifted athena
code). This adapter:

  1. Calls Factory.run(url) in a tmpdir with FDs silenced
  2. Reads arcus's .md + .json output
  3. Runs asset_download against the body so images get local copies +
     URL rewrites — matches the existing kb-capture webpage flow
  4. Composes athena's raw webpage body with the standard section layout
  5. Writes via raw_writer.write_raw under source_type=webpage

The source_type argument distinguishes regular webpages (body section
layout: "## Content") from tweets (same layout — tweets are stored as
webpages in athena's raw/, just with shorter content and inline image
refs already embedded by fetch_x_tweet's per-article DOM-order walk).

Called by bin/kb-capture's webpage / tweet / LinkedIn-in-repo branches.
See feedback-athena-arcus-split + feedback-arcus-pure-download-layer
for the governing architectural rules.

Usage:
    python3 bin/lib/arcus_html.py \\
        --vault "$KB_ROOT" --url "$URL" --date "$DATE" \\
        --source-type webpage \\
        [--desc "..."] [--keywords "..."] [--category "..."] \\
        [--skip-assets]

Prints the path of the raw artifact on success. Exits non-zero on failure.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


# Athena's lib modules live in the same directory; ensure they import.
_LIB_DIR = Path(__file__).parent
if str(_LIB_DIR) not in sys.path:
    sys.path.insert(0, str(_LIB_DIR))

from raw_writer import write_raw  # noqa: E402


@contextlib.contextmanager
def _silence_fds():
    """Redirect FD 1 + 2 to /dev/null. Mirrors arcus_video.py — yt-dlp's
    progress output isn't a problem here, but Playwright is similarly
    chatty and we don't want it polluting stdout."""
    devnull = os.open(os.devnull, os.O_WRONLY)
    saved_stdout = os.dup(1)
    saved_stderr = os.dup(2)
    try:
        os.dup2(devnull, 1)
        os.dup2(devnull, 2)
        yield
    finally:
        os.dup2(saved_stdout, 1)
        os.dup2(saved_stderr, 2)
        os.close(saved_stdout)
        os.close(saved_stderr)
        os.close(devnull)


class ArcusHtmlError(Exception):
    """Raised when arcus HTML extraction fails. Caller (kb-capture) surfaces
    this as a fatal error — no legacy inline fallback now that fetch-page.py
    is deleted in the same migration."""


def _read_arcus_outputs(out_dir: Path) -> tuple[dict[str, Any], str]:
    """Return (json_payload, md_body_text) from arcus's writer output.
    arcus writes exactly one <slug>.md + <slug>.json on success."""
    json_files = list(out_dir.glob("*.json"))
    if not json_files:
        raise ArcusHtmlError(f"arcus produced no .json sidecar in {out_dir}")
    payload = json.loads(json_files[0].read_text(encoding="utf-8"))

    md_files = list(out_dir.glob("*.md"))
    if not md_files:
        raise ArcusHtmlError(f"arcus produced no .md in {out_dir}")
    md_text = md_files[0].read_text(encoding="utf-8")

    # Strip arcus's YAML frontmatter — athena builds its own.
    body = re.sub(r"^---\n.*?\n---\n", "", md_text, count=1, flags=re.DOTALL)
    return payload, body.strip()


def _run_asset_download(
    *, vault: Path, slug: str, content: str
) -> str:
    """Pipe content through bin/lib/asset_download.py and return the rewritten
    content. Soft-fails: on any error, return original content unchanged."""
    asset_script = _LIB_DIR / "asset_download.py"
    if not asset_script.exists():
        return content

    content_tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".md", delete=False, encoding="utf-8",
    )
    try:
        content_tmp.write(content)
        content_tmp.close()
        result = subprocess.run(
            [
                sys.executable, str(asset_script),
                "--page-slug", slug,
                "--vault", str(vault),
                "--input", content_tmp.name,
            ],
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode == 0 and result.stdout:
            return result.stdout
        return content
    except (subprocess.TimeoutExpired, OSError):
        return content
    finally:
        try:
            os.unlink(content_tmp.name)
        except OSError:
            pass


_MASTHEAD_SUFFIX_RE = re.compile(r'\s*[|–—\-]\s*[^|–—]{1,40}$')


def _strip_masthead(title: str) -> str:
    """Drop a trailing site-masthead segment from a page title:
    'Article — Multi-Node … | Google Cloud Blog' → 'Article — Multi-Node …'.

    Conservative: only strips the LAST ` | segment` / ` - segment` / ` — segment`
    and only when what remains is still substantial (>= 20 chars and >= 4 words),
    so short titles or legitimately hyphenated ones are left alone.
    """
    t = title.strip()
    m = _MASTHEAD_SUFFIX_RE.search(t)
    if not m:
        return t
    head = t[:m.start()].strip()
    if len(head) >= 20 and len(head.split()) >= 4:
        return head
    return t


def _clean_webpage_body(body: str, title: str = "") -> str:
    """Strip common webpage-extraction chrome from an arcus body:

      1. A leading block of the page's own header — heading lines that duplicate
         the title (masthead suffix / '&amp;' entity and all) plus a short
         site-section breadcrumb sandwiched between them. The raw template
         already emits one '# {title}' H1, so this is redundant.
      2. Empty list bullets ('-' / '*' alone) — residue of share/nav buttons
         whose text/links the extractor dropped but whose <li> markers remain.

    Conservative: (1) only touches the TOP of the body and stops at the first
    real content line; (2) empty-bullet removal is unambiguous.
    Anchor 2026-07-01: cloud.google.com blog → title ×2 + 'Developers &
    Practitioners' crumb + four empty share bullets.
    """
    import html as _html

    def _norm(s: str) -> str:
        s = _html.unescape(s)
        s = re.sub(r'^#+\s*', '', s.strip())
        s = _strip_masthead(s)
        return re.sub(r'\s+', ' ', s).strip().lower()

    def _is_dup_title_heading(line: str) -> bool:
        s = line.strip()
        if not s.startswith('#') or not tnorm:
            return False
        n = _norm(s)
        return bool(n) and (n == tnorm or n in tnorm or tnorm in n)

    tnorm = _norm(title) if title else ""
    lines = body.split("\n")
    i = 0
    while i < len(lines):
        s = lines[i].strip()
        if not s:
            i += 1
            continue
        if _is_dup_title_heading(lines[i]):
            i += 1
            continue
        # Short breadcrumb — strip only if a duplicate-title heading follows soon.
        if (not s.startswith('#') and len(s.split()) <= 4
                and not s.endswith((".", ":", "!", "?"))):
            nxt = next((l for l in lines[i + 1:i + 4] if l.strip()), "")
            if _is_dup_title_heading(nxt):
                i += 1
                continue
        break
    kept = lines[i:]

    out = [ln for ln in kept if not re.fullmatch(r'\s*[-*]\s*', ln)]
    cleaned = re.sub(r'\n{3,}', '\n\n', "\n".join(out)).strip()
    return cleaned


def _build_athena_body(
    arcus_body: str,
    *,
    date_str: str,
    content_lines: int,
    desc_hint: str = "",
    keywords_hint: str = "",
    category_hint: str = "",
) -> str:
    """Compose athena's raw webpage body matching kb-capture's existing layout.

    Mirrors the existing bash body composition exactly so downstream wiki layer
    + lints see no structural difference between arcus-extracted and legacy
    inline-extracted webpages.
    """
    parts: list[str] = []
    parts.append(f"- **Clipped:** {date_str}")
    parts.append(f"- **Content fetched:** yes ({content_lines} lines, via arcus)")

    if desc_hint or keywords_hint or category_hint:
        parts.append("")
        parts.append("## User Suggestions")
        parts.append("")
        parts.append("<!-- These are user-provided hints. The ingest workflow will "
                     "independently analyze the content and reconcile — do not blindly "
                     "trust these. -->")
        parts.append("")
        if desc_hint:
            parts.append(f"- **Description:** {desc_hint}")
        if keywords_hint:
            parts.append(f"- **Keywords:** {keywords_hint}")
        if category_hint:
            parts.append(f"- **Category:** {category_hint}")

    parts.append("")
    parts.append("## Content")
    parts.append("")
    parts.append(arcus_body or "<!-- Content extraction returned empty -->")
    return "\n".join(parts) + "\n"


def _derive_title(arcus_title: str, body: str, url: str) -> str:
    """Title strategy mirrors kb-capture's existing webpage branch:
    arcus's title first, then first H1 in body, then first non-empty line,
    then humanized URL slug. Never returns the literal 'Untitled Page'."""
    if arcus_title and arcus_title.strip():
        return arcus_title.strip()[:200]

    # First H1 in body
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped[2:].strip()[:200] or _humanize_url(url)
        if stripped:
            return stripped[:200]

    return _humanize_url(url)


def _humanize_url(url: str) -> str:
    """Humanize a URL into a fallback title — drop scheme/host, split tokens."""
    no_scheme = re.sub(r"^https?://(www\.)?", "", url)
    no_trailing = no_scheme.rstrip("/")
    tokens = re.sub(r"[/?#&=._-]+", " ", no_trailing).split()
    if not tokens:
        return "Untitled Page"
    return " ".join(t.capitalize() for t in tokens)[:200]


def _www_variants(url: str) -> list[str]:
    """Return fetch URLs to try in order: the URL as given, plus a `www.`
    variant when the host lacks one. canonicalize() strips `www.` for dedup,
    but some hosts only serve WITH www (provos.org), so the www variant is the
    retry. Returns just [url] when a www variant doesn't apply.
    """
    from urllib.parse import urlsplit, urlunsplit

    variants = [url]
    parts = urlsplit(url)
    if parts.netloc and not parts.netloc.lower().startswith("www."):
        variants.append(urlunsplit(parts._replace(netloc="www." + parts.netloc)))
    return variants


def extract_html_url(*, url: str, deep: bool = False) -> tuple[str, list[str], dict[str, Any]]:
    """Lower-level helper used by both ingest_html_url (full write) and the
    `--print-body` CLI mode (text-only). Returns (body_text, image_urls,
    metadata_dict). Raises ArcusHtmlError on extraction failure.

    deep=True bypasses arcus's Factory and calls _athena_fetch_page directly
    with the deep flag set (longer wait + scroll-to-bottom). The Factory
    path doesn't currently expose per-call deep mode (would require an
    ExtractionContext field change across arcus's Provider Protocol —
    larger refactor). For now the deep path is athena-side only; arcus's
    HtmlProvider stays unaware, which keeps the architectural split clean.
    Witnessed 2026-05-19: Microsoft Security Blog article returned 1.2KB
    via the default path (nav only); deep mode is the right tool there."""
    if deep:
        try:
            from arcus.provider_runtime.providers.html._athena_fetch_page import (
                fetch_page, _is_x_url, fetch_x_tweet,
            )
        except ImportError as e:
            raise ArcusHtmlError(
                f"arcus-provider-runtime not installed: {e}"
            ) from e
        if _is_x_url(url):
            # X.com still uses xcom path; deep flag is ignored — tweet
            # threading is already exhaustive (per-article DOM walk).
            result = fetch_x_tweet(url) or {}
            body = result.get("text", "") or ""
            images = result.get("images") or []
        else:
            body = fetch_page(url, deep=True) or ""
            images = []
        if not body.strip():
            raise ArcusHtmlError(f"arcus deep extraction returned empty for {url}")
        return body, images, {
            "source": url, "source_id": url,
            "title": "", "slug": "",
        }

    try:
        from arcus.provider_runtime import (
            EXIT_CODES,
            Factory,
            ProviderRegistry,
            register_defaults,
        )
    except ImportError as e:
        raise ArcusHtmlError(
            f"arcus-provider-runtime not installed: {e}"
        ) from e

    registry = ProviderRegistry()
    register_defaults(registry)
    factory = Factory(registry)

    match = factory.detect(url)
    if match is None:
        raise ArcusHtmlError(f"no arcus provider matches: {url}")
    provider, _ = match
    if provider.kind != "html":
        raise ArcusHtmlError(
            f"arcus dispatched to {provider.kind} provider, not html"
        )

    # Fetch with a www-variant retry. canonicalize() strips `www.` for dedup,
    # but some hosts ONLY serve with www (provos.org → arcus exit 10 without
    # it). When the canonical (www-less) host fails, retry once with `www.`
    # prepended so www-only hosts still capture. (Anchor: provos.org, 2026-06-27.)
    fetch_variants = _www_variants(url)

    payload = arcus_body = None
    last_exit = None
    for variant in fetch_variants:
        with tempfile.TemporaryDirectory(prefix="athena-arcus-html-") as tmp:
            out_dir = Path(tmp)
            with _silence_fds():
                last_exit = factory.run(variant, out_dir=out_dir, force=False)
            if last_exit == EXIT_CODES["SUCCESS"]:
                payload, arcus_body = _read_arcus_outputs(out_dir)
                break
    if payload is None:
        raise ArcusHtmlError(
            f"arcus extraction failed (exit {last_exit}) for {url}"
        )

    metadata = payload.get("metadata") or {}
    extractor_detail = payload.get("extractor_detail") or {}
    images = extractor_detail.get("images") or []

    # reveal.js slide decks (talk pages embedded via <iframe>) aren't articles:
    # arcus either returns empty OR extracts them messily, leaving raw HTML
    # entities (&middot;, &amp;) and concatenated headings. The slide text lives
    # cleanly in the static HTML <section>s. Prefer a structured slide_deck
    # render when the page is a reveal.js deck. Gate the extra HTML fetch on a
    # cheap signal so clean captures pay nothing: an empty body, or arcus output
    # still carrying raw HTML entities (the exact mis-extraction fingerprint).
    # Single-format extraction is arcus's domain long-term; this is an
    # athena-side fallback (same precedent as the deep-mode path above).
    _entity_smell = re.search(r"&(?:middot|amp|lt|gt|nbsp|#\d+);", arcus_body)
    if not arcus_body.strip() or _entity_smell:
        try:
            import embed_discovery  # type: ignore
            import slide_deck  # type: ignore

            page_html = embed_discovery.fetch_html(url)
            deck_md = slide_deck.extract_slide_deck(page_html or "", url=url)
            if deck_md:
                arcus_body = deck_md
                if not metadata.get("title"):
                    first = deck_md.lstrip().splitlines()[0]
                    if first.startswith("# "):
                        metadata = {**metadata, "title": first[2:].strip()}
        except Exception:
            pass

    return arcus_body, images, metadata


def ingest_html_url(
    *,
    vault: Path,
    url: str,
    date_str: str,
    source_type: str = "webpage",
    desc_hint: str = "",
    keywords_hint: str = "",
    category_hint: str = "",
    skip_assets: bool = False,
    deep: bool = False,
) -> Path:
    """Extract an HTML/tweet URL via arcus + write an athena raw webpage artifact.

    source_type is currently always 'webpage' (tweets are stored as webpages
    in athena's raw/ layout — they differ only in body content shape, not in
    storage location). The arg is accepted for forward compatibility with
    future repo-specific or other dispatches.
    """
    arcus_body, images, metadata = extract_html_url(url=url, deep=deep)

    if not arcus_body.strip():
        raise ArcusHtmlError(f"arcus returned empty content for {url}")

    title = _strip_masthead(_derive_title(metadata.get("title", ""), arcus_body, url))

    # The arcus-derived slug — used for asset_download's page-slug arg so
    # local image files land under raw/assets/<slug>/.
    slug_hint = metadata.get("slug") or ""

    # Run images through asset_download (best-effort, soft-fails). The
    # downloader mutates inline image URLs in the body to point at local
    # files under raw/assets/<slug>/.
    if not skip_assets and slug_hint:
        arcus_body = _run_asset_download(
            vault=vault, slug=slug_hint, content=arcus_body,
        )

    # Strip page-header chrome (duplicate title / breadcrumb / empty share
    # bullets) the extractor carried into the body.
    arcus_body = _clean_webpage_body(arcus_body, title)

    content_lines = arcus_body.count("\n") + 1
    body = _build_athena_body(
        arcus_body,
        date_str=date_str,
        content_lines=content_lines,
        desc_hint=desc_hint,
        keywords_hint=keywords_hint,
        category_hint=category_hint,
    )

    extras: dict[str, Any] = {
        "clipped_via": "kb-capture (arcus)",
        "clipped_at": date_str,
    }
    # tweet_images for downstream: matches athena's existing convention.
    # asset_download already rewrote inline image URLs; this list is the
    # ORIGINAL remote URLs for provenance + asset-retry workflow.
    if images:
        extras["tweet_images"] = images

    try:
        raw_path = write_raw(
            vault_root=vault,
            source_type="webpage",
            url=url,
            title=title,
            body=body,
            extra=extras,
        )
    except Exception as e:
        raise ArcusHtmlError(f"athena raw_writer rejected write: {e}") from e

    return raw_path


def _cli() -> int:
    p = argparse.ArgumentParser(
        description="Ingest an HTML/tweet URL via arcus + write athena raw webpage artifact"
    )
    p.add_argument("--vault", required=True, type=Path, help="Athena vault root")
    p.add_argument("--url", required=True, help="HTML URL to extract")
    p.add_argument("--date", default="",
                   help="ISO date for clipped_at (required unless --print-body)")
    p.add_argument("--source-type", default="webpage",
                   choices=("webpage", "tweet", "repo"),
                   help="Logical source type — currently all map to webpage storage")
    p.add_argument("--desc", default="", help="User-provided description hint")
    p.add_argument("--keywords", default="", help="User-provided keywords hint")
    p.add_argument("--category", default="", help="User-provided category hint")
    p.add_argument("--skip-assets", action="store_true",
                   help="Don't run asset_download (matches ATHENA_SKIP_ASSETS env)")
    p.add_argument("--print-body", action="store_true",
                   help="Print extracted body text to stdout (no raw file written). "
                        "Used by kb-capture's tweet/LinkedIn pre-fetch paths that "
                        "need text-only access. Emits IMG: <url> lines to stderr "
                        "matching the legacy fetch-tweet.py output for compatibility.")
    p.add_argument("--deep", action="store_true",
                   help="Extended Playwright wait + scroll-to-bottom-and-back. "
                        "Use for JS-heavy articles (Microsoft Security Blog, NYT, "
                        "Medium clones) where the default capture returns nav-only "
                        "content. Slower (~10s extra per page).")
    args = p.parse_args()

    # --print-body mode: skip ALL the body composition + asset_download +
    # raw_writer pipeline. Just extract and print text to stdout.
    if args.print_body:
        if not args.vault or not args.url:
            print("arcus_html --print-body requires --url", file=sys.stderr)
            return 2
        try:
            body, images, _meta = extract_html_url(url=args.url, deep=args.deep)
        except ArcusHtmlError as e:
            print(f"arcus_html: {e}", file=sys.stderr)
            return 1
        for img in images:
            print(f"IMG: {img}", file=sys.stderr)
        print(body)
        return 0

    if not args.date:
        print("arcus_html: --date is required for full ingest", file=sys.stderr)
        return 2

    try:
        raw_path = ingest_html_url(
            vault=args.vault,
            url=args.url,
            date_str=args.date,
            source_type=args.source_type,
            desc_hint=args.desc,
            keywords_hint=args.keywords,
            category_hint=args.category,
            skip_assets=args.skip_assets,
            deep=args.deep,
        )
    except ArcusHtmlError as e:
        print(f"arcus_html: {e}", file=sys.stderr)
        return 1

    print(str(raw_path))
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
