"""embed_discovery — surface content-bearing <iframe> embeds as capturable sources.

A webpage can embed its *real* content from another URL via an iframe — most
commonly a slide deck (reveal.js, Slides, SlideShare) or an embedded document.
The browser Web Clipper cannot read inside a cross-origin iframe (same-origin
policy), and arcus's HTML extractor strips the empty <iframe> shells, so the
embedded content never enters the vault. The page lands as a thin "container"
whose actual substance is invisible.

This module closes that gap WITHOUT teaching arcus about embeds (the
athena/arcus split keeps single-URL extraction in arcus; recognizing that one
page points at OTHER capturable sources is multi-source work, which is athena's
job — see CLAUDE.md "Content extraction lives in arcus").

Flow:
  1. `extract_embed_urls(html, base_url)` — pure: pull content-embed iframe/embed
     src URLs out of raw HTML, resolve relative→absolute, drop the analytics /
     ad / social-widget / comment-widget / media-player denylist.
  2. `fetch_html(url)` — lightweight urllib GET (no browser); soft-fails to None.
  3. `discover_and_queue(vault_root, source_url)` — fetch + extract + append the
     embed URLs to inbox/url-new.txt (via canonical_source.queue_canonical_urls,
     so they get captured as their own wiki pages on the next ingest cycle) and
     record the container→embed mapping in inbox/embed-sources.tsv for the lint
     cross-reference / re-queue backstop.

Pure dependency direction:
  embed_discovery → canonical_source   (nothing in canonical_source imports this)
"""
from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urljoin, urlparse

# Hosts (matched as a substring of the netloc) whose iframes are chrome, not
# content. These are NEVER the embedded substance of a page:
#   - analytics / tag managers / ad networks
#   - social share/follow widgets
#   - comment widgets
#   - bot-check widgets
#   - media players (athena has its own video pipeline; an embedded YouTube
#     video is not a "webpage" source — queueing it here would misfile it).
_EMBED_HOST_DENYLIST = (
    "googletagmanager.com",
    "google-analytics.com",
    "googlesyndication.com",
    "googleadservices.com",
    "doubleclick.net",
    "gstatic.com",
    "google.com/recaptcha",
    "recaptcha.net",
    "hcaptcha.com",
    "facebook.com",
    "platform.twitter.com",
    "syndication.twitter.com",
    "platform.linkedin.com",
    "instagram.com",
    "giscus.app",
    "disqus.com",
    "utteranc.es",
    "youtube.com/embed",
    "youtube-nocookie.com",
    "player.vimeo.com",
    "w.soundcloud.com",
    "open.spotify.com",
    "podcasters.spotify.com",
)

# `<iframe ... src="...">` and `<embed ... src="...">`. Attribute order is not
# guaranteed, so match src anywhere inside the opening tag; quotes may be
# single or double. Non-greedy tag-interior match keeps it from spanning tags.
_EMBED_TAG_RE = re.compile(
    r"<(?:iframe|embed)\b[^>]*?\bsrc\s*=\s*(\"|')(?P<src>.*?)\1",
    re.IGNORECASE | re.DOTALL,
)


def _is_denied(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return True
    host_path = (parsed.netloc + parsed.path).lower()
    return any(bad in host_path for bad in _EMBED_HOST_DENYLIST)


def extract_embed_urls(html: str, base_url: str) -> list[str]:
    """Return content-embed (iframe/embed) source URLs found in `html`.

    Relative srcs are resolved against `base_url`. Denylisted hosts (analytics,
    ads, social/comment widgets, media players) and non-http(s) schemes
    (about:blank, data:, javascript:) are dropped. Order = first occurrence;
    deduplicated. The page's own URL is NOT filtered here — the caller's queue
    drops self-references.
    """
    if not html:
        return []
    urls: list[str] = []
    seen: set[str] = set()
    for m in _EMBED_TAG_RE.finditer(html):
        raw_src = (m.group("src") or "").strip()
        if not raw_src:
            continue
        # Resolve relative→absolute; drop the fragment — an embed's #slide=3
        # anchor is the same source. Keep the trailing slash as the site wrote
        # it (that IS the canonical URL form).
        absolute = urljoin(base_url, raw_src).split("#", 1)[0]
        if not absolute:
            continue
        if _is_denied(absolute):
            continue
        if absolute in seen:
            continue
        seen.add(absolute)
        urls.append(absolute)
    return urls


def fetch_html(url: str, timeout: int = 15) -> str | None:
    """Lightweight GET of `url` returning decoded HTML, or None on any failure.

    Deliberately NOT a browser: iframe `src` attributes live in the static
    server-rendered HTML (proven against provos.org), so urllib is enough and
    avoids a Playwright spin-up per ingest. Soft-fails on every error class so
    embed discovery can never break an ingest.
    """
    import urllib.request

    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0 Safari/537.36"
                )
            },
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            ctype = (resp.headers.get("Content-Type") or "").lower()
            if "html" not in ctype and ctype:
                return None
            raw = resp.read(5_000_000)  # 5 MB cap — embeds are in the <head>/body shell
        charset = "utf-8"
        m = re.search(r"charset=([\w-]+)", ctype)
        if m:
            charset = m.group(1)
        return raw.decode(charset, errors="replace")
    except Exception:
        return None


def _record_mapping(vault_root: Path, source_url: str, embed_urls: list[str]) -> None:
    """Append container→embed rows to inbox/embed-sources.tsv (idempotent).

    Format: <source_url>\t<embed_url>. The lint backstop reads this to (a)
    re-queue embeds that were discovered but never captured, and (b) verify the
    container and embed wiki pages cross-reference each other.
    """
    tsv = vault_root / "inbox" / "embed-sources.tsv"
    tsv.parent.mkdir(parents=True, exist_ok=True)
    existing: set[tuple[str, str]] = set()
    if tsv.exists():
        try:
            for line in tsv.read_text(encoding="utf-8").splitlines():
                parts = line.split("\t")
                if len(parts) >= 2:
                    existing.add((parts[0].strip(), parts[1].strip()))
        except (IOError, UnicodeDecodeError):
            pass
    new_rows = [
        f"{source_url}\t{u}"
        for u in embed_urls
        if (source_url, u) not in existing
    ]
    if new_rows:
        with open(tsv, "a", encoding="utf-8") as f:
            f.write("\n".join(new_rows) + "\n")


def discover_and_queue(
    vault_root: str | Path,
    source_url: str,
    html: str | None = None,
) -> list[str]:
    """Discover content-embed URLs in `source_url`'s HTML and queue them.

    If `html` is provided it is parsed directly (no network); otherwise the page
    is fetched. Discovered embeds are appended to inbox/url-new.txt (deduped
    against already-captured + already-queued via queue_canonical_urls) and the
    container→embed mapping is recorded. Returns the URLs actually queued.

    Soft-fails to [] — embed discovery is best-effort and must never break an
    ingest or a lint pass.
    """
    try:
        vault_root = Path(vault_root)
        if html is None:
            html = fetch_html(source_url)
        if not html:
            return []
        embed_urls = extract_embed_urls(html, source_url)
        if not embed_urls:
            return []
        _record_mapping(vault_root, source_url, embed_urls)
        import canonical_source  # type: ignore

        return canonical_source.queue_canonical_urls(
            vault_root, source_url, embed_urls
        )
    except Exception:
        return []
