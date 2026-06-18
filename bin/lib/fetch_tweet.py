#!/usr/bin/env python3
"""Fetch an X/Twitter status (tweet or long-form Article) cleanly, no auth.

The Obsidian plugin captures tweets by DOM-walking the rendered x.com page.
For a long-form X **Article** that fails badly: the visible tweet body is just
a `t.co` shortlink pointing at the article (`lang="zxx"` — no linguistic
content), so the walker titles the page with the shortlink and grabs only a
truncated preview. Witnessed: x.com/FakeMaidenMaker/status/2064900447375085823
(2026-06-12) — title became `https://t.co/...`, body cut off at "...".

This helper gives the plugin (and the CLI) a clean result with no `gh`-style
dependency and no Playwright: it reads X's public **syndication CDN**
(`cdn.syndication.twimg.com/tweet-result`), which returns rich JSON — real
author, full `note_tweet.text` for long tweets, `mediaDetails` images, and an
`article` block (title + preview + cover) for Articles — over plain HTTP with
no auth. Same shape as `fetch_github_readme.py`: stdlib-only, prints a complete
raw `.md` to stdout, exits 1 on failure so the caller falls back.

Usage:
    python3 fetch_tweet.py <status_url>

On success: prints the raw markdown to stdout, exits 0.
On failure (deleted/protected tweet, offline, rate-limited): prints nothing,
exits 1 — the caller falls back to its browser-capture path.
"""

import html
import json
import math
import re
import sys
import urllib.error
import urllib.request

_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
       "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
_TIMEOUT = 15
_MAX_BYTES = 8 * 1024 * 1024  # cap the syndication response (untrusted, unbounded)

# Host-anchored: the (twitter|x).com must be at the start, after a scheme/`//`,
# or after a `.` (subdomain) — so a path segment like `evil.com/x.com/u/status/1`
# is NOT mistaken for a tweet URL.
_STATUS_RE = re.compile(
    r"(?:^|https?://|//|\.)(?:twitter|x)\.com/([A-Za-z0-9_]{1,15})/status/(\d+)",
    re.IGNORECASE)


def parse_status_url(url: str) -> tuple[str, str] | None:
    """Return (handle, tweet_id) for an x.com/twitter status URL, else None."""
    m = _STATUS_RE.search(url or "")
    return (m.group(1), m.group(2)) if m else None


def _syndication_token(tweet_id: str) -> str:
    """Derive the syndication `token` query param the way x.com's own embed
    widget does: ((id / 1e15) * pi) → base36, then strip '0' digits and the
    decimal point. The endpoint currently tolerates any token, but deriving
    the real one keeps us robust if that's ever tightened."""
    v = (int(tweet_id) / 1e15) * math.pi
    digits = "0123456789abcdefghijklmnopqrstuvwxyz"
    intp = int(v)
    frac = v - intp
    s = "" if intp else "0"
    while intp:
        s = digits[intp % 36] + s
        intp //= 36
    f = ""
    for _ in range(20):
        frac *= 36
        d = int(frac)
        f += digits[d]
        frac -= d
    return re.sub(r"(0+|\.)", "", s + "." + f)


def fetch_tweet_json(tweet_id: str) -> dict | None:
    """Return the syndication CDN JSON for a tweet id, or None on any failure."""
    token = _syndication_token(tweet_id)
    url = (f"https://cdn.syndication.twimg.com/tweet-result"
           f"?id={tweet_id}&token={token}&lang=en")
    req = urllib.request.Request(url, headers={"User-Agent": _UA,
                                               "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:  # noqa: S310
            raw = resp.read(_MAX_BYTES + 1)
            if len(raw) > _MAX_BYTES:
                return None  # oversized/hostile response — treat as failure
            data = json.loads(raw.decode("utf-8", "replace"))
    except (urllib.error.URLError, OSError, ValueError):
        return None
    # A deleted/protected tweet returns an error envelope, not tweet fields.
    if not isinstance(data, dict) or not (data.get("id_str") or data.get("text") is not None
                                          or data.get("article")):
        return None
    return data


def _yaml_escape(s: str) -> str:
    # Collapse newlines BEFORE quoting: an un-stripped \n in untrusted remote
    # content (article title, author name) would break out of the quoted scalar
    # and inject arbitrary frontmatter keys into the raw .md the rest of the
    # vault trusts. Then escape backslash + double-quote.
    s = re.sub(r"[\r\n]+", " ", str(s or "")).strip()
    return s.replace("\\", "\\\\").replace('"', '\\"')


def _author(data: dict) -> tuple[str, str]:
    """Return (display_name, '@handle') from the user block."""
    u = data.get("user") or {}
    name = (u.get("name") or "").strip()
    handle = (u.get("screen_name") or "").strip()
    return name, (f"@{handle}" if handle else "")


def _expand_urls(text: str, data: dict) -> str:
    """Replace t.co shortlinks in the tweet text with their expanded targets,
    and drop the trailing t.co that merely points at the tweet's own media."""
    ents = data.get("entities") or {}
    for u in ents.get("urls") or []:
        short = u.get("url")
        expanded = u.get("expanded_url") or u.get("display_url")
        if short and expanded:
            text = text.replace(short, expanded)
    # Media t.co links carry no text value (the image is rendered separately).
    for m in ents.get("media") or []:
        short = m.get("url")
        if short:
            text = text.replace(short, "").rstrip()
    return text.strip()


def _media_markdown(data: dict) -> list[str]:
    """Return a list of markdown image lines for the tweet's photos. Videos and
    GIFs contribute their poster frame (syndication has no playable URL here)."""
    out = []
    for m in data.get("mediaDetails") or []:
        src = m.get("media_url_https")
        if not src:
            continue
        kind = m.get("type") or "photo"
        alt = "Video poster" if kind in ("video", "animated_gif") else "Image"
        out.append(f"![{alt}]({src})")
    return out


def article_title(data: dict) -> str:
    """The X Article title, or '' if this payload isn't an Article."""
    return ((data.get("article") or {}).get("title") or "").strip()


def is_x_article(data: dict) -> bool:
    """True if this syndication payload is a long-form X Article."""
    return bool((data or {}).get("article"))


def article_url(data: dict) -> str | None:
    """The canonical x.com/i/article/<id> URL for an X Article, else None.
    Mirrors the id derivation in _article_body."""
    art = (data or {}).get("article") or {}
    art_id = art.get("rest_id") or art.get("id")
    return f"https://x.com/i/article/{art_id}" if art_id else None


def _article_body(url: str, data: dict, byline: str) -> str:
    """Markdown body for a long-form X Article. The full article body is
    JS-rendered and not exposed over plain HTTP, so we capture the real title,
    cover image, author, preview, and a link to read the full piece."""
    art = data.get("article") or {}
    title = (art.get("title") or "").strip() or "X Article"
    preview = (art.get("preview_text") or "").strip()
    cover = (((art.get("cover_media") or {}).get("media_info") or {})
             .get("original_img_url"))
    _au = article_url(data)
    article_url_str = _au or url

    body = [f"# {title}", ""]
    if byline:
        body.append(f"*{byline} · X Article*")
        body.append("")
    if cover:
        body.append(f"![Cover]({cover})")
        body.append("")
    if preview:
        body.append(preview)
        body.append("")
    body.append(f"> This is a long-form X Article. Read the full piece: "
                f"[{article_url_str}]({article_url_str})")
    return "\n".join(body)


def _tweet_body(data: dict, byline: str) -> str:
    """Markdown body for a regular tweet (incl. long `note_tweet` text)."""
    note = (data.get("note_tweet") or {}).get("text")
    text = note if note else (data.get("text") or "")
    text = _expand_urls(html.unescape(text), data)

    body = []
    if byline:
        body.append(f"**{byline}**")
        body.append("")
    if text:
        body.append(text)
        body.append("")
    for img in _media_markdown(data):
        body.append(img)
        body.append("")

    quoted = data.get("quoted_tweet") or {}
    if quoted:
        q_name, q_handle = _author(quoted)
        q_text = _expand_urls(html.unescape(quoted.get("text") or ""), quoted)
        q_by = " ".join(p for p in (q_name, q_handle) if p).strip()
        if q_text or q_by:
            body.append("---")
            body.append("")
            if q_by:
                body.append(f"**Quoting {q_by}:**")
                body.append("")
            for ln in q_text.splitlines():
                body.append(f"> {ln}" if ln.strip() else ">")
            body.append("")

    return "\n".join(body).rstrip()


def build_body(url: str, data: dict) -> str:
    """Markdown body only (NO frontmatter) — the CLI's resolve_tweet adds its
    own frontmatter via wiki_schema, so it consumes the body alone."""
    name, handle = _author(data)
    byline = " ".join(p for p in (name, handle) if p).strip()
    if data.get("article"):
        return _article_body(url, data, byline)
    return _tweet_body(data, byline)


def build_raw(url: str, data: dict) -> str:
    """Assemble a complete raw .md (frontmatter + body). Used by the plugin via
    stdout; the CLI uses build_body() and supplies its own frontmatter."""
    name, handle = _author(data)
    byline = " ".join(p for p in (name, handle) if p).strip()
    is_article = bool(data.get("article"))

    if is_article:
        # Cap the title (untrusted, unbounded in the JSON) — mirrors the [:100]
        # the tweet path applies; _yaml_escape also strips embedded newlines.
        label = (article_title(data) or "X Article")[:200]
    else:
        body_text = _tweet_body(data, "")
        first_line = next((ln.strip() for ln in body_text.splitlines()
                           if ln.strip() and not ln.startswith("!")), "")
        label = (first_line[:100] or byline or "X Post").strip()

    fm = [
        "---",
        f'title: "{_yaml_escape(label)}"',
        f'source: "{_yaml_escape(url)}"',
        'clipped_via: "tweet-syndication"',
    ]
    if byline:
        fm.append(f'author: "{_yaml_escape(byline)}"')
    fm.append("---")

    return "\n".join(fm) + "\n\n" + build_body(url, data) + "\n"


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(f"usage: {argv[0]} <status_url>", file=sys.stderr)
        return 2
    parsed = parse_status_url(argv[1])
    if not parsed:
        print(f"not an x.com/twitter status URL: {argv[1]}", file=sys.stderr)
        return 2
    _handle, tweet_id = parsed
    data = fetch_tweet_json(tweet_id)
    if not data:
        return 1
    raw = build_raw(argv[1], data)
    if not raw or not raw.strip():
        return 1
    sys.stdout.write(raw)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
