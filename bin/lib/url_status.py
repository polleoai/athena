"""Keep a restored page's URL from reading `removed` in url-resolved.tsv.

`kb remove` appends a `removed` row (Operating Principle #4); `kb undo` used to
leave it as the URL's latest status while the page was back. revive_removed()
appends a `captured` row for every URL whose latest row is `removed` and whose
wiki page exists. It is called by undo (restricted to the pages it restored)
and by `kb lint` (vault-wide auto-fix). reconcile_tracking keeps the last row
per URL, so the appended row wins.

URLs are compared the way wiki_page.reconcile_tracking compares them: trailing
slash and tracking parameters stripped, lowercased.
"""
from __future__ import annotations

import os
import re
import time

TSV = os.path.join("inbox", "url-resolved.tsv")
WIKI_DIRS = ("wiki/format/webpages", "wiki/format/repos", "wiki/format/papers",
             "wiki/format/videos", "wiki/format/images")
_URL_RE = re.compile(r'^url:\s*"?(https?://\S+?)"?\s*$', re.MULTILINE)
_TRACKING_RE = re.compile(r'[?&](utm_\w+|s|t|rcm|ref|usp|si|igsh|fbclid)=[^&]*')


def norm_url(url: str) -> str:
    u = url.strip().rstrip("/")
    u = _TRACKING_RE.sub("", u)
    return re.sub(r"[?&]$", "", u).lower()


def _page_url(path: str) -> str | None:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            m = _URL_RE.search(fh.read(1000))
        return m.group(1) if m else None
    except OSError:
        return None


def revive_removed(kb_root: str, only: list[str] | None = None) -> list[str]:
    """Append `captured` rows; return the page names revived. <only> limits it
    to these vault-relative wiki paths (undo's restored files)."""
    tsv = os.path.join(kb_root, TSV)
    if not os.path.isfile(tsv):
        return []
    pages: dict[str, tuple[str, str]] = {}  # norm url -> (page name, url as on the page)
    if only is not None:
        candidates = [os.path.join(kb_root, rel) for rel in only
                      if rel.startswith("wiki/") and rel.endswith(".md")]
    else:
        candidates = []
        for d in WIKI_DIRS:
            full = os.path.join(kb_root, d)
            if os.path.isdir(full):
                candidates += [os.path.join(full, f) for f in os.listdir(full) if f.endswith(".md")]
    for path in candidates:
        url = _page_url(path) if os.path.isfile(path) else None
        if url:
            pages[norm_url(url)] = (os.path.basename(path)[:-3], url)
    if not pages:
        return []
    last: dict[str, str] = {}
    with open(tsv, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            parts = line.rstrip("\n").split("\t")
            if len(parts) >= 3 and parts[0] and not parts[0].startswith(("#", "status")):
                last[norm_url(parts[2])] = parts[0]
    revived = [(n, pages[n]) for n in pages if last.get(n) == "removed"]
    if not revived:
        return []
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    with open(tsv, "rb") as fh:  # never glue a row onto an unterminated last line
        fh.seek(0, os.SEEK_END)
        lead = ""
        if fh.tell():
            fh.seek(-1, os.SEEK_END)
            lead = "" if fh.read(1) == b"\n" else "\n"
    with open(tsv, "a", encoding="utf-8") as fh:
        fh.write(lead)
        for _n, (name, url) in sorted(revived, key=lambda x: x[1][0]):
            fh.write(f"captured\t{name}\t{url}\t{stamp}\n")
    return sorted(name for _n, (name, _u) in revived)
