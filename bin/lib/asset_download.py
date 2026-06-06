#!/usr/bin/env python3
"""
asset_download.py — Download remote images referenced in captured markdown,
store them under raw/assets/<page-slug>/, and rewrite the markdown to point
at local relative paths.

Why local copies:
  - Availability: original CDNs restructure / 404 over time
  - Safety: hot-linked images are re-resolved by Obsidian on every render,
    so a later URL takeover (compromise, redirect, DNS handover) silently
    changes what the vault displays without modifying any vault file
  - Provenance: the original URL stays recorded in a sidecar so the local
    copy can always be traced back to its source

Storage layout:
  raw/assets/<page-slug>/<sha256-of-content>.<ext>   actual bytes
  raw/assets/<page-slug>/_assets.json                sidecar metadata

Sidecar format:
  {
    "page_slug": "...",
    "captured_at": "2026-04-30T22:45:00Z",
    "assets": [
      {"local": "<sha>.<ext>", "url": "https://...", "alt": "...",
       "bytes": 12345, "content_type": "image/png", "sha256": "..."}
    ],
    "failures": [
      {"url": "https://...", "alt": "...", "error": "404", "tried_at": "..."}
    ]
  }

Usage (CLI):
    cat raw_markdown.md | asset_download.py \\
        --page-slug "the-vercel-breach-..." \\
        --vault /path/to/athena \\
        --base-url https://www.trendmicro.com/...

    Rewritten markdown goes to stdout. Failures appended to
    inbox/asset-retry.tsv (one row per failed URL).

Failure mode: soft-fail. If a download fails, the original remote URL is left
in place in the markdown so the page is still readable. The failure is logged
to inbox/asset-retry.tsv for later batch retry via `kb retry-assets`.
"""

import argparse
import base64
import datetime
import hashlib
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path

# Limits — tunable via env vars but defaulted to safe values.
MAX_BYTES = int(os.environ.get("ATHENA_ASSET_MAX_BYTES", 25 * 1024 * 1024))  # 25 MB
TIMEOUT_SECONDS = int(os.environ.get("ATHENA_ASSET_TIMEOUT", 15))
USER_AGENT = "Athena/1.0 (knowledge-base; +local-archival)"

# Mime → extension fallback. Athena prefers extracting extension from URL
# path, but CDNs often omit one (e.g. `?format=jpg&name=large` query strings).
MIME_EXT = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/gif": "gif",
    "image/webp": "webp",
    "image/svg+xml": "svg",
    "image/avif": "avif",
    "image/x-icon": "ico",
    "image/vnd.microsoft.icon": "ico",
}

# Markdown image pattern: ![alt](url)  — capture alt and url separately.
# We deliberately do NOT match HTML <img> tags here; arcus's html2md (run
# inside HtmlProvider) already converted those to markdown form.
IMG_RE = re.compile(r'!\[([^\]]*)\]\(([^)\s]+)(?:\s+"[^"]*")?\)')


def _ext_from_url(url: str) -> str:
    """Best-effort extension from URL path, lowercased, no leading dot."""
    path = urllib.parse.urlparse(url).path
    _, _, last = path.rpartition("/")
    if "." in last:
        ext = last.rsplit(".", 1)[-1].lower()
        # Strip query-string-ish junk (defensive — urlparse already cleaned)
        ext = re.sub(r"[^a-z0-9]", "", ext)
        if 1 <= len(ext) <= 5:
            return ext
    return ""


def _ext_from_content_type(ct: str) -> str:
    if not ct:
        return ""
    ct = ct.split(";", 1)[0].strip().lower()
    return MIME_EXT.get(ct, "")


def _is_downloadable_url(url: str) -> bool:
    """Reject data:, javascript:, and non-http(s) schemes."""
    if not url:
        return False
    scheme = urllib.parse.urlparse(url).scheme.lower()
    return scheme in ("http", "https")


_DATA_URI_RE = re.compile(r"^data:([^;,]+)?(?:;([^,]+))?,(.*)$", re.DOTALL)


def _data_uri_mime(uri: str) -> str:
    m = _DATA_URI_RE.match(uri)
    return (m.group(1) or "").strip() if m else ""


def _decode_data_uri(uri: str) -> tuple[bytes, str] | None:
    """Decode `data:image/...;base64,xxxx` (or non-base64) into (bytes, ext).

    Returns None if the URI is malformed or the encoding can't be decoded.
    Extension is derived from the MIME type via MIME_EXT, falling back to
    "bin" if unrecognized.
    """
    m = _DATA_URI_RE.match(uri)
    if not m:
        return None
    mime = (m.group(1) or "").strip().lower()
    encoding = (m.group(2) or "").strip().lower()
    payload = m.group(3) or ""
    try:
        if "base64" in encoding:
            data = base64.b64decode(payload, validate=False)
        else:
            data = urllib.parse.unquote_to_bytes(payload)
    except (ValueError, base64.binascii.Error):
        return None
    if not data:
        return None
    ext = MIME_EXT.get(mime, "")
    if not ext and "/" in mime:
        ext = mime.split("/", 1)[1].split("+", 1)[0]
        ext = re.sub(r"[^a-z0-9]", "", ext)[:5] or "bin"
    return data, ext or "bin"


def _download(url: str) -> tuple[bytes, str]:
    """Fetch URL bytes + content-type. Raises on any failure."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
        ct = resp.headers.get("Content-Type", "") or ""
        # Read with a hard cap so a misbehaving server can't OOM us.
        data = resp.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            raise ValueError(f"exceeds max size {MAX_BYTES} bytes")
        return data, ct


def _log_failure(vault: Path, page_slug: str, url: str, alt: str, error: str) -> None:
    """Append one row to inbox/asset-retry.tsv for later batch retry."""
    retry_path = vault / "inbox" / "asset-retry.tsv"
    retry_path.parent.mkdir(parents=True, exist_ok=True)
    new_file = not retry_path.exists()
    with retry_path.open("a", encoding="utf-8") as f:
        if new_file:
            f.write("page_slug\turl\talt\terror\ttried_at\n")
        ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        # Tabs and newlines in alt would corrupt the TSV — collapse them.
        alt_clean = re.sub(r"[\t\n\r]+", " ", alt or "").strip()
        err_clean = re.sub(r"[\t\n\r]+", " ", error or "").strip()
        f.write(f"{page_slug}\t{url}\t{alt_clean}\t{err_clean}\t{ts}\n")


def download_assets(markdown: str, page_slug: str, vault: Path) -> str:
    """Walk markdown, download each image, rewrite to local relative path.

    Returns rewritten markdown. Always succeeds — individual failures keep
    the original URL in place and append to inbox/asset-retry.tsv.

    Path style chosen: relative path from raw/webpages/artifacts/foo.md to
    raw/assets/<slug>/<file> is `../../assets/<slug>/<file>`. We use that
    same form whether the caller is a webpage, repo, or other raw type that
    sits at depth 3 (raw/<cat>/artifacts/<file>.md). Raw files at depth 2
    (raw/<cat>/<file>.md) would need `../assets/...` instead — currently
    only artifacts/ depth is in scope; revisit if other capture flows hook
    in.
    """
    assets_dir = vault / "raw" / "assets" / page_slug
    sidecar_path = assets_dir / "_assets.json"

    sidecar = {
        "page_slug": page_slug,
        "captured_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "assets": [],
        "failures": [],
    }
    # Cache: same URL appearing N times in the page → download once.
    seen: dict[str, str] = {}  # url -> local filename

    def _replace(match: re.Match) -> str:
        alt = match.group(1)
        url = match.group(2)

        # `data:` URI images: decode and save as a local asset file.
        # Mixed bag in practice — pages use them for both empty SVG
        # placeholders (lazy-load stubs JS would replace) AND real
        # content (small inline diagrams/thumbnails). Earlier we just
        # stripped them all, which lost the SITF diagram on the wiz.io
        # article. Saving everything is the safer default — empty
        # placeholders are tiny (~200 bytes) and harmless, while real
        # content is preserved.
        if url.startswith("data:"):
            decoded = _decode_data_uri(url)
            if decoded is None:
                return ""  # malformed/unparseable — drop the reference
            data, ext = decoded
            sha = hashlib.sha256(data).hexdigest()
            local_name = f"{sha}.{ext or 'bin'}"
            assets_dir.mkdir(parents=True, exist_ok=True)
            local_path = assets_dir / local_name
            if not local_path.exists():
                local_path.write_bytes(data)
            sidecar["assets"].append({
                "local": local_name,
                "url": "data:" + url[5:url.index(",")] if "," in url else url[:50],
                "alt": alt,
                "bytes": len(data),
                "content_type": _data_uri_mime(url),
                "sha256": sha,
                "origin": "inline-data-uri",
            })
            return f"![{alt}](../../assets/{page_slug}/{local_name})"

        if not _is_downloadable_url(url):
            return match.group(0)  # keep javascript:, mailto:, etc. (rare in <img>)

        # Already-local references shouldn't be touched.
        if url.startswith(("../", "./", "/")) or not url.startswith(("http://", "https://")):
            return match.group(0)

        if url in seen:
            local_name = seen[url]
            return f"![{alt}](../../assets/{page_slug}/{local_name})"

        try:
            data, content_type = _download(url)
        except Exception as e:  # network, HTTP, size, timeout — all soft-fail
            err = f"{type(e).__name__}: {e}"
            sidecar["failures"].append({
                "url": url, "alt": alt, "error": err,
                "tried_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            })
            _log_failure(vault, page_slug, url, alt, err)
            return match.group(0)  # keep remote URL as fallback

        sha = hashlib.sha256(data).hexdigest()
        ext = _ext_from_url(url) or _ext_from_content_type(content_type) or "bin"
        local_name = f"{sha}.{ext}"
        assets_dir.mkdir(parents=True, exist_ok=True)
        local_path = assets_dir / local_name
        # Content-addressed: if a file with this hash+ext already exists, reuse.
        if not local_path.exists():
            local_path.write_bytes(data)
        seen[url] = local_name
        sidecar["assets"].append({
            "local": local_name,
            "url": url,
            "alt": alt,
            "bytes": len(data),
            "content_type": content_type.split(";", 1)[0].strip(),
            "sha256": sha,
        })
        return f"![{alt}](../../assets/{page_slug}/{local_name})"

    rewritten = IMG_RE.sub(_replace, markdown)

    # Dedupe consecutive references to the same image. HTML often emits
    # responsive variants (`<picture>` source + img, or
    # `<img srcset="...">` mobile/desktop pairs) that render as ONE image
    # but parse to multiple `<img src="">` tags. After content-addressing,
    # those variants collapse to the same local file, leaving the
    # markdown with N identical `![](url)` references that Obsidian
    # renders as N copies of the same image — wiz.io's hero image showed
    # up 4 times because of this.
    #
    # The dedupe scope is "same image, same line OR adjacent line" — we
    # don't dedupe across paragraphs, since the same image legitimately
    # appearing twice in a long article is fine to preserve.
    def _is_image_only(line: str) -> bool:
        return bool(IMG_RE.search(line)) and not IMG_RE.sub("", line).strip()

    def _dedupe_block(block: str) -> str:
        seen: list[str] = []
        for m in IMG_RE.finditer(block):
            ref = m.group(0)
            if ref not in seen:
                seen.append(ref)
        non_img = IMG_RE.sub("", block).strip()
        if non_img:
            return block
        return " ".join(seen)

    # Walk lines and group runs where image-only lines are separated by at
    # most blank lines. Responsive variants (`<picture>` source + img,
    # mobile + desktop pairs) often render as `<img>...</img>` on
    # consecutive lines with blank-line spacing — they all hash to the
    # same content-addressed file and should collapse to one reference.
    lines = rewritten.split("\n")
    out_lines: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if _is_image_only(line):
            group_end = i
            j = i + 1
            while j < len(lines):
                if _is_image_only(lines[j]):
                    group_end = j; j += 1
                elif lines[j].strip() == "":
                    j += 1  # skip blank, keep looking for more image-only
                else:
                    break
            if group_end > i:
                block = " ".join(l for l in lines[i:group_end + 1] if l.strip())
                out_lines.append(_dedupe_block(block))
                i = group_end + 1
                continue
            else:
                out_lines.append(_dedupe_block(line))
                i += 1
                continue
        out_lines.append(line)
        i += 1
    rewritten = "\n".join(out_lines)

    # Cosmetic cleanup: collapse runs of 3+ blank lines to 2 (Markdown
    # convention — 2 blanks = paragraph break is enough). Image strips
    # in earlier versions left noisy blank-line gaps; this normalizes
    # them whether the source was a strip-removal or a normally
    # paragraph-spaced markdown file.
    rewritten = re.sub(r"\n{4,}", "\n\n\n", rewritten)

    # Persist sidecar only if we actually attempted any image work.
    if sidecar["assets"] or sidecar["failures"]:
        assets_dir.mkdir(parents=True, exist_ok=True)
        sidecar_path.write_text(json.dumps(sidecar, indent=2, ensure_ascii=False), encoding="utf-8")

    return rewritten


# Source-bearing wiki subdirectories that may need their bodies updated
# after a backfill rewrites raw asset references. Mirrors the list in
# wiki_page._WIKI_SOURCE_DIRS — kept separate to avoid an import cycle.
_WIKI_SOURCE_DIRS_FOR_BACKFILL = (
    'wiki/format/webpages',
    'wiki/format/repos',
    'wiki/format/papers',
    'wiki/format/videos',
    'wiki/format/images',
    'wiki/format/books',
)


def find_wiki_for_raw(rel_raw_path: str, vault: Path) -> Path | None:
    """Locate the wiki page whose frontmatter `raw_path` matches the
    given relative raw path. Returns absolute Path or None.

    Used by `kb backfill-assets` to propagate raw rewrites into the
    matching wiki body. Does NOT scan topic / insight / entity pages —
    those don't mirror raw sources."""
    for sub in _WIKI_SOURCE_DIRS_FOR_BACKFILL:
        sub_dir = vault / sub
        if not sub_dir.is_dir():
            continue
        for wp in sub_dir.iterdir():
            if not wp.name.endswith('.md'):
                continue
            try:
                head = wp.read_text(encoding='utf-8', errors='replace')[:2048]
            except Exception:
                continue
            m = re.search(r'^raw_path:\s*"?([^"\n]+?)"?\s*$', head, re.MULTILINE)
            if m and m.group(1).strip() == rel_raw_path:
                return wp
    return None


def rewrite_wiki_body_assets(slug: str, vault: Path) -> int:
    """After `kb backfill-assets` rewrites raw asset refs to the
    raw-relative form `../../assets/<slug>/<file>`, propagate the same
    URL → local-filename mapping into the matching wiki page's body
    using the wiki-relative form `../../../raw/assets/<slug>/<file>`.

    Returns the number of asset-reference replacements made (0 if no
    sidecar, no matching wiki page, or wiki body already in sync).

    The wiki form has one extra `../` because wiki pages live at
    `wiki/format/<cat>/<file>.md` (depth 3) vs raws at
    `raw/<cat>/artifacts/<file>.md` (also depth 3, but on the OTHER
    side of the vault root) — the relative path crosses the root.
    Matches `_rewrite_asset_paths_for_wiki` in wiki_page.py.
    """
    sidecar = vault / 'raw' / 'assets' / slug / '_assets.json'
    if not sidecar.exists():
        return 0
    try:
        data = json.loads(sidecar.read_text(encoding='utf-8'))
    except Exception:
        return 0
    url_to_local = {a.get('url'): a.get('local')
                    for a in data.get('assets', [])
                    if a.get('url') and a.get('local')}
    if not url_to_local:
        return 0
    rel_raw = f'raw/webpages/artifacts/{slug}.md'
    wiki_path = find_wiki_for_raw(rel_raw, vault)
    if not wiki_path:
        return 0
    body = wiki_path.read_text(encoding='utf-8')
    new_body = body
    replaced = 0
    for url, local in url_to_local.items():
        wiki_rel = f'../../../raw/assets/{slug}/{local}'
        # Match `](url)` so we hit both `![alt](url)` image refs and
        # plain `[text](url)` links that pointed at the asset. Bounded
        # by `]` and `(` so we don't accidentally match the URL inside
        # an unrelated context (e.g. a quoted JSON example body).
        old = f']({url})'
        new = f']({wiki_rel})'
        if old in new_body:
            replaced += new_body.count(old)
            new_body = new_body.replace(old, new)
    if new_body != body:
        wiki_path.write_text(new_body, encoding='utf-8')
    return replaced


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    p.add_argument("--page-slug", required=True, help="Slug of the page being captured (subdir under raw/assets/)")
    p.add_argument("--vault", required=True, help="Absolute path to the Athena vault root")
    p.add_argument("--input", default="-", help="Markdown input file (default: stdin)")
    args = p.parse_args()

    vault = Path(args.vault).resolve()
    if not vault.is_dir():
        sys.stderr.write(f"asset_download: vault not found: {vault}\n")
        sys.exit(2)

    if args.input == "-":
        markdown = sys.stdin.read()
    else:
        markdown = Path(args.input).read_text(encoding="utf-8")

    out = download_assets(markdown, args.page_slug, vault)
    sys.stdout.write(out)


if __name__ == "__main__":
    main()
