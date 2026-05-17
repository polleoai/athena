"""Canonical URL handling — single source of truth.

Before this module existed, URL normalization was scattered across:
  * canonical_source.py — arXiv ID normalization, DOI arXiv-alias collapse,
                          HuggingFace /blob/→/resolve/ transform,
                          GitHub /blob/→raw.githubusercontent.com transform
  * wiki_schema.py:_url_to_slug_input — strip query params for slug input
  * kb-capture:232-238 — strip tracking params via sed pipeline
  * kb_commands.py:236 — arxiv canonical URL builder

Each had subtly different rules, which is why we kept seeing duplicate
pages from the same source URL with different tracking params, broken
PDF fetches from /blob/ wrapper pages, and arxiv-DOI-alias dups.

This module is the consolidation. Phase 1 of the schema refactor: the
contract is defined here; existing call sites still use their own logic.
Phase 3 migrates each call site to use canonicalize() instead. Once a
call site is migrated, the corresponding lint section becomes redundant.

Pure functions, no I/O. Safe to import from anywhere.
"""

from __future__ import annotations

import re
import urllib.parse
from enum import Enum
from typing import NamedTuple


# ─── Source kinds ────────────────────────────────────────────────────


class SourceKind(str, Enum):
    """Coarse classification of a URL by its host. Drives slug-derivation
    policy (URL_DERIVED for social/repo, TITLE_DERIVED allowed for paper),
    routing decisions in capture, and lint-rule applicability.

    Add new kinds when a new host needs distinct handling. Don't conflate
    distinct hosts under OTHER if they need different canonicalization.
    """

    ARXIV = "arxiv"
    GITHUB = "github"
    HUGGINGFACE = "huggingface"
    X = "x"  # twitter.com or x.com
    LINKEDIN = "linkedin"
    SUBSTACK = "substack"
    MEDIUM = "medium"
    YOUTUBE = "youtube"
    GOOGLE_DRIVE = "google_drive"
    DOI = "doi"
    OTHER = "other"


# ─── Tracking param denylist ─────────────────────────────────────────
# Strip these unconditionally. The list mirrors kb-capture:238 and adds
# the ad-network params commonly seen in shared links.
_TRACKING_PARAMS = frozenset({
    "utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term",
    "rcm", "ref", "usp", "s", "t",
    "fbclid", "gclid", "mc_eid", "mc_cid", "_ga", "_gl",
    "share", "share_via", "share_id",
    "igshid", "feature",
})

# Hosts where ALL query params are tracking noise. Stripping all is safer
# than enumerating every param Twitter/LinkedIn invents next quarter.
_STRIP_ALL_PARAMS_HOSTS = frozenset({
    "x.com", "twitter.com",
    "linkedin.com",
    "substack.com",  # plus *.substack.com (handled in _strip_query)
    "medium.com",
})

# Hosts where SPECIFIC params are essential and must be preserved.
# Everything else is dropped.
_ESSENTIAL_PARAMS_BY_HOST = {
    "youtube.com": frozenset({"v", "list", "t"}),  # 't' is timestamp here, not tracking
    "youtu.be": frozenset({"t"}),
}


# ─── Helpers ─────────────────────────────────────────────────────────


def _strip_query(host: str, query: str) -> str:
    """Apply tracking-param denylist with host-specific overrides."""
    if not query:
        return ""
    pairs = urllib.parse.parse_qsl(query, keep_blank_values=False)
    if not pairs:
        return ""

    # Substack's actual host is <publication>.substack.com — match suffix.
    base_host = host
    if base_host.endswith(".substack.com"):
        base_host = "substack.com"

    if base_host in _STRIP_ALL_PARAMS_HOSTS:
        return ""

    if base_host in _ESSENTIAL_PARAMS_BY_HOST:
        keep = _ESSENTIAL_PARAMS_BY_HOST[base_host]
        kept = [(k, v) for k, v in pairs if k in keep]
        return urllib.parse.urlencode(kept)

    kept = [(k, v) for k, v in pairs if k not in _TRACKING_PARAMS]
    return urllib.parse.urlencode(kept)


_ARXIV_ID_RE = re.compile(r"^(\d{4}\.\d{4,5})(?:v\d+)?$")
_ARXIV_PATH_RE = re.compile(r"^/(?:abs|pdf|html)/(\d{4}\.\d{4,5})(?:v\d+)?(?:\.pdf)?$")
_DOI_ARXIV_ALIAS_RE = re.compile(r"^/10\.48550/arXiv\.(\d{4}\.\d{4,5})$", re.IGNORECASE)


def _normalize_arxiv(parsed: urllib.parse.ParseResult) -> urllib.parse.ParseResult | None:
    """Return canonical arxiv URL, or None if not arxiv-shaped."""
    host = parsed.netloc.lower().removeprefix("www.")
    if host == "arxiv.org":
        m = _ARXIV_PATH_RE.match(parsed.path)
        if m:
            return parsed._replace(
                netloc="arxiv.org",
                path=f"/abs/{m.group(1)}",
                query="",
                fragment="",
            )
    if host in ("doi.org", "dx.doi.org"):
        m = _DOI_ARXIV_ALIAS_RE.match(parsed.path)
        if m:
            return urllib.parse.urlparse(f"https://arxiv.org/abs/{m.group(1)}")
    return None


def _normalize_pdf_host(parsed: urllib.parse.ParseResult) -> urllib.parse.ParseResult:
    """Transform HTML-wrapper URLs into raw-bytes URLs for known PDF hosts.

    HuggingFace: /<owner>/<repo>/blob/<branch>/<file> serves an HTML
        viewer, not the PDF. The /resolve/ path serves bytes. Capture
        pipelines that don't transform this end up ingesting the wrapper
        page as 'webpage' instead of routing to the paper handler.

    GitHub: /<owner>/<repo>/blob/<branch>/<file> is the HTML preview;
        raw.githubusercontent.com/<owner>/<repo>/<branch>/<file> is bytes.
    """
    host = parsed.netloc.lower().removeprefix("www.")
    if host == "huggingface.co":
        m = re.match(r"^(/[^/]+/[^/]+)/blob/(.+)$", parsed.path, re.IGNORECASE)
        if m:
            return parsed._replace(path=f"{m.group(1)}/resolve/{m.group(2)}")
    if host == "github.com":
        m = re.match(r"^/([^/]+)/([^/]+)/blob/(.+)$", parsed.path, re.IGNORECASE)
        if m:
            owner, repo, rest = m.group(1), m.group(2), m.group(3)
            return parsed._replace(
                netloc="raw.githubusercontent.com",
                path=f"/{owner}/{repo}/{rest}",
            )
    return parsed


# ─── Public API ──────────────────────────────────────────────────────


def source_kind(url: str) -> SourceKind:
    """Coarse classification by host. Used by slug policy + capture routing."""
    try:
        host = urllib.parse.urlparse(url).netloc.lower().removeprefix("www.")
    except Exception:
        return SourceKind.OTHER
    if host == "arxiv.org":
        return SourceKind.ARXIV
    if host in ("github.com", "raw.githubusercontent.com"):
        return SourceKind.GITHUB
    if host == "huggingface.co":
        return SourceKind.HUGGINGFACE
    if host in ("x.com", "twitter.com"):
        return SourceKind.X
    if host == "linkedin.com" or host.endswith(".linkedin.com"):
        return SourceKind.LINKEDIN
    if host.endswith(".substack.com") or host == "substack.com":
        return SourceKind.SUBSTACK
    if host == "medium.com" or host.endswith(".medium.com"):
        return SourceKind.MEDIUM
    if host in ("youtube.com", "youtu.be", "m.youtube.com"):
        return SourceKind.YOUTUBE
    if host in ("drive.google.com", "docs.google.com"):
        return SourceKind.GOOGLE_DRIVE
    if host in ("doi.org", "dx.doi.org"):
        return SourceKind.DOI
    return SourceKind.OTHER


class CanonicalizeResult(NamedTuple):
    """Return type of canonicalize() — surfaces what changed for diagnostics."""

    url: str
    kind: SourceKind
    changed: bool


_LINKEDIN_FEED_UPDATE_RE = re.compile(
    r"^/feed/update/urn:li:(?:ugcPost|activity):(\d+)/?$",
    re.IGNORECASE,
)
# Author-handle form: /posts/<author>_<slug>-ugcPost-<NUMERIC_ID>-<variant>
# Also matches activity-<ID>- and share-<ID>- forms LinkedIn uses for older
# posts. The URN <NUMERIC_ID> is the stable identity; everything else is
# decoration that varies by share path / Web Clipper paste form / mobile
# vs desktop URL.
_LINKEDIN_POSTS_AUTHOR_RE = re.compile(
    r"^/posts/[^/]*-(?:ugcPost|activity|share)-(\d+)(?:-[A-Za-z0-9_-]+)?/?$",
    re.IGNORECASE,
)
# URN-only canonical form (what we're collapsing TO). Already canonical;
# match-and-passthrough so we don't double-process.
_LINKEDIN_POSTS_URN_ONLY_RE = re.compile(
    r"^/posts/(?:ugcpost|activity|share)-(\d+)/?$",
    re.IGNORECASE,
)


def _normalize_linkedin(parsed):
    """Collapse LinkedIn URL variants to the stable URN-only form
    `/posts/ugcpost-<NUMERIC_ID>` (or `/posts/activity-<ID>` / `/posts/share-<ID>`
    for legacy URN types).

    LinkedIn exposes the same post under multiple URL forms — they all
    embed the same numeric URN, but with varying decoration:

      Author-handle form (Web Clipper / share button):
        /posts/<author>_<title-slug>-ugcPost-<ID>-<variant>
      Feed-update form (in-app share):
        /feed/update/urn:li:ugcPost:<ID>/
      URN-only form (canonical target):
        /posts/ugcpost-<ID>

    All three collapse to URN-only — the numeric ID is the stable
    identity, everything else is decoration that varies by share path,
    paste form, mobile vs desktop, etc.

    Without this collapse, two clips of the same post via different URL
    forms produce two wiki pages — the duplicate-page bug class
    surfaced 0.9.13 (Praneeta) and again 0.10.11 (the same post
    re-captured under both /posts/<author> and /posts/ugcpost forms).
    """
    if not (parsed.netloc == "linkedin.com" or parsed.netloc.endswith(".linkedin.com")):
        return parsed
    # Form 1: /feed/update/urn:li:<type>:<ID>/
    m = _LINKEDIN_FEED_UPDATE_RE.match(parsed.path)
    if m:
        return parsed._replace(path=f"/posts/ugcpost-{m.group(1)}", query="", fragment="")
    # Form 2: /posts/<author>_<slug>-ugcPost-<ID>-<variant>
    m = _LINKEDIN_POSTS_AUTHOR_RE.match(parsed.path)
    if m:
        return parsed._replace(path=f"/posts/ugcpost-{m.group(1)}", query="", fragment="")
    # Form 3: /posts/ugcpost-<ID> — already canonical, just normalize
    # case (LinkedIn URLs are case-insensitive; ugcPost vs ugcpost).
    m = _LINKEDIN_POSTS_URN_ONLY_RE.match(parsed.path)
    if m:
        return parsed._replace(path=f"/posts/ugcpost-{m.group(1)}", query="", fragment="")
    return parsed


def canonicalize(raw_url: str) -> CanonicalizeResult:
    """Return the canonical form of `raw_url`.

    Steps:
        1. Lowercase scheme + host. Strip `www.` prefix.
        2. Strip tracking params (host-specific policy).
        3. Apply arXiv normalization (DOI alias collapse, version strip).
        4. Apply PDF-host transform (HF /blob/→/resolve/, GH /blob/→raw).
        5. LinkedIn /feed/update/ → /posts/ugcpost-<id> (added 0.9.13).
        6. Trim trailing slash on path (except root).

    Idempotent: canonicalize(canonicalize(x).url).url == canonicalize(x).url.
    Pure: no network, no filesystem, no exceptions on bad input (returns
    a best-effort result with kind=OTHER and changed=False).
    """
    if not raw_url or not isinstance(raw_url, str):
        return CanonicalizeResult(url=raw_url or "", kind=SourceKind.OTHER, changed=False)

    try:
        parsed = urllib.parse.urlparse(raw_url.strip())
    except Exception:
        return CanonicalizeResult(url=raw_url, kind=SourceKind.OTHER, changed=False)

    if not parsed.scheme or not parsed.netloc:
        return CanonicalizeResult(url=raw_url, kind=SourceKind.OTHER, changed=False)

    # 1. Lowercase scheme + host, strip www.
    scheme = parsed.scheme.lower()
    netloc = parsed.netloc.lower().removeprefix("www.")
    parsed = parsed._replace(scheme=scheme, netloc=netloc)

    # 3. arXiv normalization first — may change netloc + path before query strip
    arxiv_result = _normalize_arxiv(parsed)
    if arxiv_result is not None:
        parsed = arxiv_result

    # 4. PDF-host transform
    parsed = _normalize_pdf_host(parsed)

    # 5. LinkedIn /feed/update/ → /posts/ugcpost-<id> normalization
    parsed = _normalize_linkedin(parsed)

    # 2. Strip tracking params (after host transforms so per-host rules apply
    #    to the post-transform host)
    new_query = _strip_query(parsed.netloc, parsed.query)
    parsed = parsed._replace(query=new_query, fragment="")

    # 5. Trim trailing slash (except root)
    if parsed.path.endswith("/") and parsed.path != "/":
        parsed = parsed._replace(path=parsed.path.rstrip("/"))

    canonical = urllib.parse.urlunparse(parsed)
    return CanonicalizeResult(
        url=canonical,
        kind=source_kind(canonical),
        changed=(canonical != raw_url),
    )


# ─── Self-test ───────────────────────────────────────────────────────
# Run as: python3 bin/lib/url_canonical.py


if __name__ == "__main__":
    cases = [
        # (input, expected_url, expected_kind)
        (
            "https://www.linkedin.com/posts/brijpandeyji_millions-of-people-7455470610769485824-tF2k/?utm_source=social_share_send&utm_medium=ios_app&rcm=ACoA",
            "https://linkedin.com/posts/brijpandeyji_millions-of-people-7455470610769485824-tF2k",
            SourceKind.LINKEDIN,
        ),
        (
            "https://x.com/regent0x_/status/2049499354323399002?s=12&t=SqfI4NPSdgelyhHpiHqK3Q",
            "https://x.com/regent0x_/status/2049499354323399002",
            SourceKind.X,
        ),
        (
            "https://arxiv.org/abs/2405.21060v3",
            "https://arxiv.org/abs/2405.21060",
            SourceKind.ARXIV,
        ),
        (
            "https://doi.org/10.48550/arXiv.2602.09433",
            "https://arxiv.org/abs/2602.09433",
            SourceKind.ARXIV,
        ),
        (
            "https://huggingface.co/deepseek-ai/DeepSeek-V4-Pro/blob/main/DeepSeek_V4.pdf",
            "https://huggingface.co/deepseek-ai/DeepSeek-V4-Pro/resolve/main/DeepSeek_V4.pdf",
            SourceKind.HUGGINGFACE,
        ),
        (
            "https://github.com/anthropics/skills/blob/main/README.md",
            "https://raw.githubusercontent.com/anthropics/skills/main/README.md",
            SourceKind.GITHUB,
        ),
        (
            "https://github.com/anthropics/skills",
            "https://github.com/anthropics/skills",
            SourceKind.GITHUB,
        ),
        (
            "https://www.youtube.com/watch?v=abc123&utm_source=share",
            "https://youtube.com/watch?v=abc123",
            SourceKind.YOUTUBE,
        ),
    ]

    failed = 0
    for raw, expected_url, expected_kind in cases:
        result = canonicalize(raw)
        if result.url != expected_url:
            print(f"FAIL url:\n  in:  {raw}\n  out: {result.url}\n  exp: {expected_url}")
            failed += 1
        elif result.kind != expected_kind:
            print(f"FAIL kind:\n  url: {raw}\n  out: {result.kind}\n  exp: {expected_kind}")
            failed += 1
        else:
            # Verify idempotent
            second = canonicalize(result.url)
            if second.url != result.url:
                print(f"FAIL idempotent:\n  in:  {result.url}\n  out: {second.url}")
                failed += 1
    if failed:
        print(f"\n{failed} failures out of {len(cases)} cases")
        raise SystemExit(1)
    print(f"All {len(cases)} canonicalize cases passed.")
