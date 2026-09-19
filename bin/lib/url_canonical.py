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

import os
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
    "rcm", "usp",
    # `s`, `t` and `ref` used to live here. They are share tracking on x.com
    # and LinkedIn — and those hosts strip EVERY param below, so nothing is
    # lost by removing them from the global list. On an arbitrary host a
    # one-letter param is just as likely to BE the identity
    # (example.com/view?s=A7KD92), and deleting it silently collapses every
    # such URL onto one key. A name that short cannot carry a global policy.
    # `usp` stays: it appears 3x in this vault's corpus (Google Drive) and is
    # tracking in all three; identity there lives in the /file/d/<id>/ path.
    "fbclid", "gclid", "mc_eid", "mc_cid", "_ga", "_gl",
    "share", "share_via", "share_id",
    "igshid", "feature",
})

_TRACKING_PARAMS_LOWER = frozenset(p.lower() for p in _TRACKING_PARAMS)

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
    # True when canonicalization appears to have thrown away the part of the
    # URL that named the content. Defaults False so existing 3-tuple callers
    # and `._replace` sites keep working unchanged.
    identity_lost: bool = False


# Paths that name a place to look rather than a thing to read. A canonical
# that lands on one of these, after a non-tracking query was dropped, is a
# dedup key shared by every URL that ever pointed into that view.
_CONTENTLESS_PATHS = frozenset({
    "", "/feed", "/feed/update", "/home", "/explore",
    "/search", "/timeline", "/dashboard",
})

# A stored raw `source:` that is a known social feed/site root. Narrower than
# _CONTENTLESS_PATHS on purpose: lint sees only the stored URL, with no memory
# of what was dropped to produce it, so a bare homepage there is a legitimate
# capture rather than a defect. Owned here, imported by the lint check.
SOCIAL_ROOT_SOURCE_RE = re.compile(
    r'^https?://(?:www\.)?('
    r'linkedin\.com/feed|linkedin\.com|'
    r'x\.com/home|x\.com|twitter\.com/home|twitter\.com|'
    r'medium\.com|substack\.com|news\.ycombinator\.com|reddit\.com'
    r')/?$',
    re.IGNORECASE,
)


def _identity_lost(original, canonical_parsed) -> bool:
    """Did canonicalization drop the component that named the content?

    Two independent signals, either of which is enough:

    1. A route-shaped fragment (`#/...` or `#!...`). `canonicalize` clears the
       fragment unconditionally, which is right for a documentation anchor
       (`#installation`) and wrong for a client-side route, where the fragment
       IS the address. The leading `/` or `!` is what separates the two.

    2. A query carrying something outside the tracking denylist was dropped,
       AND what remains is a content-less path. Requiring both keeps the flag
       quiet on the healthy cases: a homepage with only `?utm_source=` has no
       residual, and `youtube.com/watch?v=x` keeps both its param and a real
       path.
    """
    frag = (original.fragment or "").strip()
    if frag[:1] in ("/", "!"):
        return True

    if not original.query or canonical_parsed.query:
        return False
    residual = [
        k for k, _ in urllib.parse.parse_qsl(original.query)
        if k.lower() not in _TRACKING_PARAMS_LOWER
    ]
    if not residual:
        return False
    return canonical_parsed.path.rstrip("/").lower() in _CONTENTLESS_PATHS


# Notification / email form: /feed/?highlightedUpdateUrn=urn:li:<type>:<ID>
# The post's identity lives ONLY in the query here. `linkedin.com` is in
# _STRIP_ALL_PARAMS_HOSTS, so unless this is lifted into the path BEFORE
# _strip_query runs, the URL collapses to the bare feed root and every such
# link shares one dedup key. Matched against the parsed query value, so the
# %3A-encoded and literal-colon spellings both land here.
_LINKEDIN_HIGHLIGHTED_URN_RE = re.compile(
    r"^urn:li:(?:ugcPost|activity|share):(\d+)$",
    re.IGNORECASE,
)
_LINKEDIN_FEED_PATH_RE = re.compile(r"^/feed/?$", re.IGNORECASE)
# Query params LinkedIn uses to name a post. Compared case-insensitively.
# Kept as a set rather than one param: the notification, share and in-app
# update links each use a different name for the same thing.
_LINKEDIN_URN_PARAMS = frozenset({
    "highlightedupdateurn", "shareurn", "updateurn",
})

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

    # Form 4: identity in the QUERY — /feed/?highlightedUpdateUrn=<urn> and the
    # shareUrn / updateUrn spellings of the same thing. Lifted into the path
    # here, BEFORE _strip_query deletes it (linkedin.com strips every param).
    #
    # Ordered LAST on purpose: a path that already names a post is
    # authoritative, so a stray URN param can never hijack it. And because the
    # lift rewrites the path and clears the query, strip-all stays the backstop
    # — no raw param ever survives into a canonical, which is what would
    # reintroduce the duplicate-page class this function exists to prevent.
    if parsed.query:
        for key, value in urllib.parse.parse_qsl(parsed.query):
            if key.lower() not in _LINKEDIN_URN_PARAMS:
                continue
            m = _LINKEDIN_HIGHLIGHTED_URN_RE.match(value.strip())
            if m:
                return parsed._replace(
                    path=f"/posts/ugcpost-{m.group(1)}", query="", fragment="")
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

    original = parsed  # pre-transform, for the identity-loss check below

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
        identity_lost=_identity_lost(original, parsed),
    )


IDENTITYLESS_OVERRIDE_ENV = "ATHENA_ALLOW_IDENTITYLESS"


def identity_loss_notice(raw_url: str) -> str | None:
    """The human-readable diagnosis for a URL that lost its identity, or None.

    Every ingest entry point needs the same diagnosis; they differ only in what
    they DO with it (refuse vs warn), so the wording lives here and cannot
    drift between them. Pure: no env, no IO — `identity_loss_refusal` applies
    the policy.
    """
    result = canonicalize(raw_url)
    if not result.identity_lost:
        return None
    return (
        f"this URL's identity is not recoverable from its canonical form: "
        f"{raw_url}\n"
        f"  canonicalizes to: {result.url}\n"
        f"  That is a shared key, not this item — it names a view, so every "
        f"URL pointing into that view collapses onto it. Capturing it would "
        f"fetch whatever the view happens to show now.\n"
        f"  Open the item and copy its own permalink, then add that.\n"
        f"  To capture the view itself anyway: "
        f"{IDENTITYLESS_OVERRIDE_ENV}=1"
    )


def identity_loss_refusal(raw_url: str) -> str | None:
    """`identity_loss_notice`, unless the caller opted out via the env var.

    For entry points that FETCH from the URL (kb add, unified ingest), where a
    shared key means fetching the wrong thing. The Web Clipper uses the notice
    directly — it already holds content the browser pulled, so refusing there
    would discard a good capture to prevent a naming problem.
    """
    if os.environ.get(IDENTITYLESS_OVERRIDE_ENV):
        return None
    return identity_loss_notice(raw_url)


def resolvable_url(raw_url: str) -> str:
    """Return a URL the origin host will actually SERVE for `raw_url`.

    Counterpart to `canonicalize`, which produces the dedup *identity*. They
    differ for LinkedIn's notification/email form
    `/feed/?highlightedUpdateUrn=urn:li:<type>:<ID>`, where navigating the URL
    as given renders the whole feed with the post merely highlighted. Captured
    anonymously that is a login wall; captured with a session it is a feed
    snapshot of whatever else happened to be in the timeline — the origin of
    the "LinkedIn Feed Snapshot" pages holding unrelated posts.

    Rewrite it to the single-post permalink `/feed/update/urn:li:<type>:<ID>/`,
    which LinkedIn serves. The URN *type* is preserved here even though
    `canonicalize` discards it: `activity` and `ugcPost` are separate
    namespaces, and the wrong one returns "Invalid post link".

    Every other URL is returned unchanged. Pure: no network, no filesystem.
    """
    if not raw_url or not isinstance(raw_url, str):
        return raw_url
    try:
        parsed = urllib.parse.urlparse(raw_url.strip())
    except Exception:
        return raw_url
    host = parsed.netloc.lower().removeprefix("www.")
    if not (host == "linkedin.com" or host.endswith(".linkedin.com")):
        return raw_url
    if not (_LINKEDIN_FEED_PATH_RE.match(parsed.path) and parsed.query):
        return raw_url
    for key, value in urllib.parse.parse_qsl(parsed.query):
        if key.lower() != "highlightedupdateurn":
            continue
        m = _LINKEDIN_HIGHLIGHTED_URN_RE.match(value.strip())
        if m:
            urn_type = value.strip().split(":")[2]
            return (f"https://www.linkedin.com/feed/update/"
                    f"urn:li:{urn_type}:{m.group(1)}/")
        break
    return raw_url


def is_unservable_canonical(canonical_url: str) -> bool:
    """True if `canonical_url` is a dedup KEY the origin host does not serve.

    Most canonical forms are just the original minus tracking/www — still
    clickable. LinkedIn is the exception: `_normalize_linkedin` collapses the
    `/posts/<author>_<slug>-{share,activity,ugcPost}-<id>` forms to a synthetic
    `/posts/ugcpost-<id>` key. That key is stable for dedup, but LinkedIn does
    NOT serve it — opening it returns "Invalid post link" (the URN *type* was
    thrown away, so a `share`/`activity` id sits in the `ugcpost-` slot). Such
    canonicals are fine as identity but must not be shown as the clickable
    `source:` link; callers should record the original resolvable URL instead.
    """
    if not canonical_url or not isinstance(canonical_url, str):
        return False
    try:
        parsed = urllib.parse.urlparse(canonical_url)
    except Exception:
        return False
    if not (parsed.netloc == "linkedin.com" or parsed.netloc.endswith(".linkedin.com")):
        return False
    return bool(_LINKEDIN_POSTS_URN_ONLY_RE.match(parsed.path))


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
