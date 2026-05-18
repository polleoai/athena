"""Process one Web Clipper clip into a typed raw artifact.

Replaces the bin/kb shell clipper loop (sed-based filename slug + cp).
Reads the clip's Web Clipper frontmatter, extracts the canonical URL,
runs it through the typed write API.

Web Clipper drops .md files with frontmatter like:

    ---
    title: "Post | LinkedIn"
    source: "https://www.linkedin.com/posts/<author>_<slug>-<id>-..."
    author:
    published:
    created: 2026-05-09
    description:
    tags:
      - "clippings"
    ---

Used by `kb add` (the clipper-loop branch) and called from the post-clip
hook in server.py. Single source of truth for clip → raw conversion.

Pure on inputs except the final atomic write. Returns the path of the
raw artifact written, or raises ProcessClipError.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import time
import unicodedata
from pathlib import Path

from raw_parser import read_raw_frontmatter
from raw_writer import RawWriterError, write_raw
from url_canonical import canonicalize, source_kind, SourceKind


# Domains that the Web Clipper handles unreliably (URN-only URL forms,
# missing image refs, generic titles, no author attribution). For these
# we ALWAYS prefer capture-deep — Web Clipper just becomes a URL trigger.
# Keep this list narrow: only add a domain after observing Web Clipper
# fail it. As of 0.10.14: LinkedIn proven broken (Praneeta + Kumo cases).
# X.com works fine via Web Clipper per user testing 2026-05-10.
_PLAYWRIGHT_DOMAINS = ("linkedin.com",)

_AUTH_MARKER = Path.home() / ".athena" / "playwright-userdata" / ".athena-auth-confirmed"


def _should_promote_to_playwright(canonical_url: str, fm: dict) -> bool:
    """Return True iff this clip should be re-fetched via capture-deep
    instead of using the Web Clipper body.

    Four guards in series:
      1. ATHENA_DISABLE_PLAYWRIGHT_PROMOTE env var is unset (test escape hatch)
      2. URL host matches a known-broken domain
      3. Clip didn't already come from capture-deep (avoid recursion)
      4. Playwright auth marker exists (saved LinkedIn session)

    A False return for reason #4 is a SETUP issue worth surfacing —
    `_warn_if_setup_needed` handles that side; this function just
    returns False so the caller falls through to the Web Clipper path."""
    # Test escape hatch: tests run in temp vaults but the auth marker lives
    # in the user's $HOME, so a developer's marker file leaks into every
    # test. Setting ATHENA_DISABLE_PLAYWRIGHT_PROMOTE=1 disables the
    # promote path entirely. Production never sets this.
    if os.environ.get("ATHENA_DISABLE_PLAYWRIGHT_PROMOTE"):
        return False
    if not _is_promote_eligible_domain(canonical_url):
        return False
    if (fm.get("clipped_via") or "").strip() == "deep-capture":
        return False  # already capture-deep output; don't re-promote
    if not _AUTH_MARKER.exists():
        return False  # no Playwright auth set up yet
    return True


def _is_promote_eligible_domain(canonical_url: str) -> bool:
    """URL host matches a domain we'd auto-promote IF auth were set up."""
    return any(domain in canonical_url for domain in _PLAYWRIGHT_DOMAINS)


def _warn_if_setup_needed(canonical_url: str, fm: dict, clip_name: str) -> None:
    """If the URL would have been promoted but auth is missing, write a
    one-time-per-URL warning to stderr so the user knows their LinkedIn
    capture is going to be thin. We still write the Web Clipper raw —
    something is better than nothing — but the warning gives the user
    the action to take."""
    if (fm.get("clipped_via") or "").strip() == "deep-capture":
        return
    if not _is_promote_eligible_domain(canonical_url):
        return
    if _AUTH_MARKER.exists():
        return
    # Auth not set up. Surface the action.
    print(
        f"[process_clip] {clip_name}: source URL is on the auto-promote "
        f"domain list ({canonical_url}) but Playwright auth is not set up. "
        f"Web Clipper will be used as a fallback (likely thin capture). "
        f"To enable richer captures: run `bin/kb capture-deep --setup` "
        f"once to log into LinkedIn. Subsequent captures will auto-promote.",
        file=sys.stderr,
    )


def _run_capture_deep(url: str, vault_root: Path) -> Path | None:
    """Invoke `bin/kb capture-deep <url>` and return the path of the
    new clip it wrote. Returns None on failure (capture-deep exited
    non-zero, didn't print a path, or printed a non-existent path).
    The calling code falls back to the Web Clipper raw on None."""
    kb_bin = vault_root / "bin" / "kb"
    if not kb_bin.exists():
        return None
    try:
        result = subprocess.run(
            [str(kb_bin), "capture-deep", url],
            capture_output=True,
            text=True,
            timeout=120,  # 2 min cap; capture-deep usually < 30s
            env={**os.environ, "KB_ROOT": str(vault_root)},
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    if result.returncode != 0:
        return None
    # capture-deep prints the new clip path on stdout (single line).
    # stderr carries progress messages.
    new_clip = result.stdout.strip()
    if not new_clip:
        return None
    new_path = Path(new_clip)
    if not new_path.is_file():
        return None
    return new_path


# Bait titles that the Web Clipper extracts from chrome-stripped page
# titles (every LinkedIn post has `<title>Post | LinkedIn</title>`,
# so propagating that verbatim creates collision-bait raw titles).
# Lowercase comparison.
_BAIT_CLIP_TITLES = frozenset({
    "post | linkedin",
    "feed | linkedin",
    "sign up | linkedin",
    "linkedin",
    "log in | linkedin",
    "login | linkedin",
    "home | x",
    "x",
    "feed | x",
    "untitled",
    "untitled page",
    "page",
    "post",
})

# Pattern-based bait titles. The exact-match _BAIT_CLIP_TITLES set
# above doesn't generalize over a unique value-bearing token, so add
# regex matches for shapes we know are chrome but vary by author/post:
#
#   "View Zhaorun Chen's profile"   ← LinkedIn profile-link chrome
#   "View Praneeta D.'s profile"
#
# Surfaced 0.10.16 — Zhaorun Chen's wiki page landed with the literal
# "View Zhaorun Chen's profile" as title because the chrome strip
# fallback couldn't run on a Web Clipper raw that didn't have a
# `## Feed post` marker (the strip's primary anchor).
#
# 0.10.19: added LinkedIn premium-upsell sidebar phrases. These appear
# in the user's authenticated DOM sidebar (Achieve 4x more profile
# visits / Reactivate Premium / etc.) and can leak into title when
# Branch B chrome-strip fires too early on a "Follow" word in the
# user chrome (e.g., "Follow back" / "Follow companies"), leaving
# the premium markers in the body before Branch C can clean them.
_BAIT_CLIP_TITLE_PATTERNS = (
    re.compile(r"^view\s+.+?(?:'s|’s|s)\s+profile$", re.IGNORECASE),
    # Premium-upsell sidebar phrases — symmetric to Branch C markers
    re.compile(r"^achieve\s+\d+x?\s+more\s+profile\s+visits?$", re.IGNORECASE),
    re.compile(r"^reactivate\s+premium\b.*$", re.IGNORECASE),
    re.compile(r"^profile\s+viewers?$", re.IGNORECASE),
    re.compile(r"^post\s+impressions?$", re.IGNORECASE),
    re.compile(r"^premium:\s*\d+%\s*off$", re.IGNORECASE),
)


def _is_bait_title(title: str) -> bool:
    """Return True iff `title` is a known chrome-bait title that the
    Web Clipper extracts but isn't representative of the actual post
    content. Combines the exact-match set + regex patterns."""
    norm = (title or "").strip().lower()
    if norm in _BAIT_CLIP_TITLES:
        return True
    for pattern in _BAIT_CLIP_TITLE_PATTERNS:
        if pattern.match(norm):
            return True
    return False


def _normalize_unicode_typography(text: str) -> str:
    """Normalize math-bold/italic Unicode to ASCII.

    LinkedIn posts often use Unicode mathematical alphanumeric symbols
    (ranges U+1D400–U+1D7FF) for emphasis: 𝗖𝗹𝗮𝘂𝗱𝗲𝗕𝗹𝗲𝗲𝗱 → ClaudeBleed.
    These render as bold-sans in some viewers, garbled in others, and
    explode wiki page filenames. Use NFKD then drop combining marks to
    fold them back to plain ASCII while preserving non-Latin scripts.
    """
    if not text:
        return text
    # NFKD decomposes math-bold/italic forms to base ASCII + style marks.
    # Combining marks (style indicators) get dropped via category check.
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def _derive_title_from_body(body: str, max_len: int = 80) -> str:
    """Pick the first plausible sentence from a stripped clip body.

    Used when the Web Clipper title is collision-bait (Post | LinkedIn).
    Strips markdown formatting (bold/italic markers, link wrappers) and
    Unicode typography before scanning so the result is a clean ASCII
    title suitable for both raw frontmatter AND wiki filename.
    """
    if not body:
        return ""
    # Strip markdown emphasis / links so the scan sees plain text
    cleaned = re.sub(r"\*\*?([^*]+)\*\*?", r"\1", body)
    cleaned = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", cleaned)
    cleaned = _normalize_unicode_typography(cleaned)

    candidates = []
    for line in cleaned.split("\n"):
        line = line.strip()
        if not line or len(line) < 20:
            continue
        if line.startswith(("http", "!", "#", "-", "*", ">")):
            continue
        candidates.append(line)

    def _truncate_clean(text: str, limit: int) -> str:
        """Truncate at limit, but back up to the last word boundary so the
        title doesn't end mid-word ('AI agents ... tools for them are sti').
        Prefers the last sentence-end (`.!?`) before the limit if one
        exists in the back third — that's a more natural cut than a
        word boundary. 0.10.21: fix for Zhaorun-class titles where the
        first-sentence-as-title pattern produced 'sti' / 'tomorr' / etc.
        suffixes from byte-precise truncation."""
        if len(text) <= limit:
            return text
        truncated = text[:limit]
        # Prefer last sentence terminator in the back third
        for terminator in (". ", "! ", "? "):
            cut = truncated.rfind(terminator)
            if cut >= limit * 0.66:
                return truncated[:cut + 1]
        # Fall back to last word boundary
        last_space = truncated.rfind(" ")
        if last_space >= limit * 0.66:
            return truncated[:last_space].rstrip(" .,;:")
        return truncated  # candidate has no spaces in last 27 chars — give up

    # Two-pass selection. LinkedIn `<main>.innerText` includes profile
    # headline chrome that survives Branch C's strip when the post
    # author's bio sits between the user-profile-sidebar markers and
    # the actual post body. These bios are typically multi-comma noun
    # phrases without terminal punctuation ("Technologist, Investor,
    # entrepreneur" / "CS PhD Student at The University of Chicago").
    # Prefer a real sentence (has terminal `.`/`!`/`?`) over a phrase
    # candidate. Bug surfaced 0.10.18 (Zhaorun re-capture title bait).
    def _looks_like_headline_chrome(text: str) -> bool:
        """A multi-comma noun phrase WITHOUT terminal punctuation —
        almost always a LinkedIn profile headline / job title chrome."""
        if not re.search(r"[.!?]", text):
            # No sentence punctuation at all
            comma_count = text.count(",")
            if comma_count >= 1 and len(text.split()) <= 8:
                return True  # short, comma-separated → headline-style
        return False

    # Pass 1: prefer real sentences (skip headline-chrome candidates)
    for line in candidates:
        if _looks_like_headline_chrome(line):
            continue
        m = re.search(r"^(.+?[.!?])\s", line + " ")
        candidate = m.group(1).strip() if m else line
        return _truncate_clean(candidate, max_len)
    # Pass 2: fallback to any candidate (better than no title)
    for line in candidates:
        m = re.search(r"^(.+?[.!?])\s", line + " ")
        candidate = m.group(1).strip() if m else line
        return _truncate_clean(candidate, max_len)
    return ""


class ProcessClipError(ValueError):
    """Raised when a clip cannot be turned into a valid raw artifact."""


# Map URL source_kind → raw artifact category. GitHub URLs land in
# raw/repos/, video hosts in raw/videos/, everything else webpages.
# Papers via this path are atypical — kb-capture handles arxiv directly.
_KIND_TO_CATEGORY = {
    SourceKind.GITHUB: "repo",
    SourceKind.YOUTUBE: "video",
    # All others: webpage (X, LinkedIn, Substack, Medium, HF, etc.)
}


# ─── LinkedIn chrome stripping ─────────────────────────────────────
#
# LinkedIn's logged-in DOM puts the user's own profile sidebar, premium
# upsells (Reactivate Premium / Achieve 4x more profile visits), saved-items
# nav, and group/newsletter links above every post. Below the post, it
# appends comment/reaction navigation. The Web Clipper grabs all of it.
#
# Without stripping, every LinkedIn raw starts with the user's name + photo
# (privacy concern) and ends with bracket-chain link spam (noise that
# pollutes Local Copy view + LLM summary input).
#
# The post itself is sandwiched between `## Feed post` (LinkedIn's own
# DOM section header for the actual post body) and `## Most relevant`
# (the comments section header). Strip outside that band.

_LINKEDIN_LEADING_MARKER = re.compile(
    r'^##\s+Feed post\b.*$', re.MULTILINE
)

# Plain-text equivalent for capture-deep clips (where <main>.innerText
# collapses markdown headers to plain text). The "Feed post" line on
# its own, surrounded by blank lines.
_LINKEDIN_LEADING_MARKER_PLAIN = re.compile(
    r'\n\s*Feed post\s*\n', re.IGNORECASE
)

# Post-author-header end marker: LinkedIn's "Connect" button always
# appears on its own line right after the author block (Name, degree,
# bio, post-age) and right BEFORE the post body. Matching this is more
# reliable than guessing where the user-profile chrome ends — works
# for both Web Clipper and capture-deep clips of authenticated DOM.
# Constrain to the first 2000 chars to avoid matching "Connect" / "Follow"
# if they appear as English words in the post body itself. LinkedIn shows
# different variants depending on the relationship between the post
# author and the viewing user:
#   - "Connect" — author is not in the user's network and not followed
#   - "Follow"  — author IS followed OR has too many connections to add
#   - "Following" — author IS being followed (the "unfollow" toggle)
# All three are end-of-author-header markers; treat identically.
# 0.10.18: added "Follow" / "Following" after Zhaorun re-capture surfaced
# a Follow-button-only authenticated DOM where Branch B couldn't anchor.
_LINKEDIN_CONNECT_BUTTON = re.compile(
    r'\n\s*(?:Connect|Follow|Following)\s*\n', re.IGNORECASE
)
_LINKEDIN_TRAILING_MARKER = re.compile(
    # First of any of these signals the start of post-content chrome.
    # `#{0,2}` allows the marker to appear with 0, 1, or 2 leading hashes
    # (LinkedIn renders some footers as plain text, others as headers).
    # NOTE: `##?` would mean "one # followed by optional #" — not what we want.
    # Order doesn't matter — re.search finds earliest match across alternation.
    r'\n#{0,2}\s*Most relevant\b'
    r'|\n#{0,2}\s*Be the first to comment\b'        # empty-comments footer
    r'|\n#{0,2}\s*\d+\s+comments?\b'                # populated-comments footer
    r'|\n\d+\s+reactions?\b'                        # reactions count line
    r'|\n<iframe\s'                                 # ad/promo iframe
    # Added in 0.9.9 after the Jim Libby clip surfaced more chrome variants:
    r'|\nEnjoy this\?\s+Repost it to your network\b'  # LinkedIn auto-CTA
    r'|\nTo view or add a comment\b'                # auth-wall comment CTA
    r'|\n#{0,2}\s*More from this author\b'          # related-posts header
    r'|\n#{0,2}\s*Explore content categories\b'     # content categories footer
    r'|\n[\d,]+\s+followers\b'                      # author profile sidebar
    r'|\n\d+\s*\nLike\s*\nComment\s*\nShare\b'      # reactions-count + action row
    # Added in 0.9.14 — carousel-preview block (post-title echo + bullet + N pages):
    #   AI Attack Surface Compounding
    #   ·
    #   8 pages
    # The bullet (·, U+00B7) on its own line followed by "N pages" is
    # distinctive enough that a body line never matches by accident.
    # Walk back to include the preview title that sits just before the bullet.
    r'|\n[^\n]+\n+\s*·\s*\n+\s*\d+\s+pages\b',
    re.IGNORECASE,
)
_LINKEDIN_PROFILE_NAV_LINE = re.compile(
    # The "View X's profile" rendered link that immediately follows the
    # ## Feed post marker (LinkedIn renders the profile photo as both an
    # image-link in the marker line AND as a text repeat below).
    r"^(?:View\s+[^\n]+?\s+profile\s*\n+){1,2}",
    re.MULTILINE,
)


_LINKEDIN_POST_IMAGE = re.compile(
    # Match `![alt](url)` for any HTTPS image whose URL doesn't contain
    # "profile-displayphoto" / "profile-framedphoto" / "company-logo" —
    # the LinkedIn profile/company chrome image patterns. Was originally
    # restricted to `media.licdn.com/` exactly, but capture-deep observed
    # different CDN hosts (`dms.licdn.com`, `static.licdn.com`, occasional
    # cross-region variants), so the strict host check was dropping post
    # content images. 0.10.21: broadened to "any HTTPS image that isn't
    # one of the named chrome-photo patterns" — matches what capture-deep's
    # scrapeImages() filter does on the JS side.
    r'!\[[^\]]*\]\(https://(?![^)]*(?:profile-displayphoto|profile-framedphoto|company-logo))[^)]+\)',
    re.IGNORECASE,
)


# Twitter/X image URLs come in size variants via the `name=` query param:
# small (~680px), medium (~1200px), large (full ~2000px), 4096x4096 (orig).
# Web Clipper grabs `name=large` by default — way too big for Obsidian's
# reader view, where the image takes over the viewport and hides body
# text. We rewrite to `name=medium` (good display quality, ~half the
# bandwidth) AND add Obsidian's `|600` width constraint inside the alt
# text so even if the URL rewrite missed an edge case, Obsidian still
# renders at 600px. Pure display fix; the source image is unchanged on
# Twitter's side.
_TWIMG_MD_IMG_RE = re.compile(
    r'!\[([^\]]*)\]\((https?://pbs\.twimg\.com/[^)]+?)\)',
    re.IGNORECASE,
)
_TWIMG_HTML_IMG_RE = re.compile(
    r'<img\b[^>]*\bsrc=["\'](https?://pbs\.twimg\.com/[^"\']+)["\'][^>]*>',
    re.IGNORECASE,
)
_TWIMG_NAME_PARAM_RE = re.compile(r'([?&]name=)(large|4096x4096|orig)\b', re.IGNORECASE)

def _rewrite_twimg_images(body: str) -> str:
    """Downsize Twitter/X `pbs.twimg.com` image refs and constrain
    display width to 600px via an HTML `<img>` tag.

    Why HTML and not markdown `![alt|600](url)`: Obsidian honors the
    `|width` alt-text syntax ONLY in Reading View (and internal `![[ ]]`
    embeds). Live Preview / Edit Mode renders external image markdown
    at native pixel size with no width constraint — a `name=medium`
    image (~1200px) still takes over the typical 800px editor pane.
    HTML `<img width="...">` is honored in BOTH modes.

    Two rewrites:
      1. Markdown `![alt](pbs.twimg.com/...)` → HTML `<img>` with
         width=600, name=medium URL, alt preserved.
      2. Existing HTML `<img src="pbs.twimg.com/..."` (rare in
         Web Clipper output but possible) → ensure width=600 and
         name=medium. Idempotent.

    Strips Web Clipper junk attributes (`tabindex`, `disableremoteplayback`,
    `style="..."`, etc.) — those are screen-reader / video-player flags
    that don't belong in our raw archive.
    """
    def _md_sub(m):
        alt, url = m.group(1), m.group(2)
        new_url = _TWIMG_NAME_PARAM_RE.sub(r'\1medium', url)
        # Escape `"` inside alt so the attribute stays valid HTML.
        # Strip any leftover `|<width>` syntax from the alt text — this
        # is the old markdown-form width hint that we used briefly in
        # 1.0.12; it's meaningless in HTML alt and reads like noise.
        alt_clean = re.sub(r'\|\d+\s*$', '', alt).strip() or 'Image'
        alt_attr = alt_clean.replace('"', '&quot;')
        return f'<img src="{new_url}" alt="{alt_attr}" width="600">'

    def _html_sub(m):
        url = m.group(1)
        new_url = _TWIMG_NAME_PARAM_RE.sub(r'\1medium', url)
        # Reduce to a minimal canonical tag — keep src + alt + width,
        # drop any other attrs that were on the original.
        alt_match = re.search(r'\balt=["\']([^"\']*)["\']', m.group(0), re.IGNORECASE)
        alt_attr = (alt_match.group(1) if alt_match else 'Image').replace('"', '&quot;')
        return f'<img src="{new_url}" alt="{alt_attr}" width="600">'

    body = _TWIMG_MD_IMG_RE.sub(_md_sub, body)
    body = _TWIMG_HTML_IMG_RE.sub(_html_sub, body)
    return body


# `<video>` elements captured from X.com (and similar) carry inline
# `style="position: absolute; top: 0%; left: 0%; ..."` AND a `<source>`
# with a `blob:` URL. Two problems together:
#   1. The blob: URL is page-local — won't load anywhere outside the
#      original tweet's runtime context. The video element renders as
#      an empty box.
#   2. The absolute positioning makes that empty box OVERLAY whatever
#      content is in the same screen region — you see the empty player
#      sitting on top of body text.
#
# These tags are useless in our archive. Strip them, leaving the
# sibling poster <img> (which Web Clipper helpfully captures next to
# every video) plus a "Watch on source" link so the user can click
# through to the original tweet for the actual video.
_BLOB_VIDEO_RE = re.compile(
    r'<video\b[^>]*>\s*<source\b[^>]*\bsrc=["\']blob:[^"\']*["\'][^>]*>\s*</video>',
    re.IGNORECASE | re.DOTALL,
)

def _strip_blob_videos(body: str, source_url: str = "") -> str:
    """Remove <video>...</video> blocks whose <source> uses a blob: URL.
    If we know the source page URL, leave a "[Watch video on source]"
    link in their place so the user has a path to the actual video.
    """
    if source_url:
        replacement = f'\n[Watch video on source]({source_url})\n'
    else:
        replacement = ''
    return _BLOB_VIDEO_RE.sub(replacement, body)


def _strip_linkedin_chrome(body: str) -> str:
    """Strip LinkedIn's logged-in chrome from a clip body.

    Leading + trailing strips run independently: leading needs the
    `## Feed post` marker; trailing matches a comment/reaction/CTA
    footer pattern. A clip with only one of the two markers gets the
    matching half stripped. A clip with neither passes through unchanged
    (conservative — never drop content we can't positively identify
    as LinkedIn chrome).

    Post images are pre-extracted before stripping and re-appended to
    the cleaned body. This is necessary because Web Clipper concatenates
    `![View image](feedshare-url)` onto the same line as trailing chrome
    like "Enjoy this? Repost it to your network and follow X for more" —
    the trailing-marker cut would otherwise discard the image with the
    chrome. Position-in-flow is lost (images get re-attached at the end),
    but LinkedIn posts conventionally place their content image at or
    near the end anyway. (Bug surfaced 0.9.11.)
    """
    # Pre-extract post-content images so the trailing strip doesn't take
    # them with the chrome they're concatenated to.
    post_images = _LINKEDIN_POST_IMAGE.findall(body)

    leading = _LINKEDIN_LEADING_MARKER.search(body)
    if leading:
        body = body[leading.end():].lstrip("\n ")
    # Plain-text "Feed post" marker fallback for capture-deep clips
    # where <main>.innerText collapsed the markdown header. Symmetric
    # to Branch A but matches plain text. Runs even if Branch A fired
    # (idempotent — won't re-match anything since Branch A already
    # consumed the heading).
    plain = _LINKEDIN_LEADING_MARKER_PLAIN.search(body)
    if plain:
        body = body[plain.end():].lstrip("\n ")
        leading = plain  # mark for the unchanged-body check below
    # Branch B: even after a Feed-post cut, the author header (Name,
    # degree, bio, post-age, Connect/Follow button) is still leading
    # chrome. Find the FIRST Connect/Follow/Following line in the first
    # ~2000 chars and strip everything before+including it. Constrain
    # by position to avoid matching the word in the actual post body.
    # 0.10.20: un-nested from the `else:` block so it runs after Branch A
    # too. Capture-deep clips always include `## Feed post` as a header
    # AND the user-profile sidebar BETWEEN that header and the post body
    # — Branch A alone wouldn't strip the sidebar; Branch B does.
    connect_search_window = body[:2000]
    connect = _LINKEDIN_CONNECT_BUTTON.search(connect_search_window)
    if connect:
        body = body[connect.end():].lstrip("\n ")
        leading = connect or leading  # preserve any earlier leading mark
    # Branch C (0.10.17, 0.10.19, 0.10.20): scan for LinkedIn premium-
    # sidebar markers ("Reactivate Premium", "Profile viewers", "Post
    # impressions"). These appear ONLY in the authenticated user's own
    # profile chrome (not in the post body itself). When detected, strip
    # up to the FIRST substantive content line following the last marker
    # — defined as a line >= 80 chars with at least one terminal
    # punctuation. The Zhaorun re-capture (capture-deep with
    # <main>.innerText fallback) surfaced this case.
    # 0.10.20: dedented out of the old `else:` block — runs after Branches
    # A AND B too, because capture-deep clips have `## Feed post` (Branch
    # A fires) AND user-sidebar chrome between the header and the post
    # body. Branches A and B alone don't strip the sidebar chrome.
    sidebar_markers = (
        "Reactivate Premium",
        "Achieve 4x more profile visits",
        "Profile viewers",
        "Post impressions",
        "Premium: 50% Off",
    )
    sidebar_window = body[:2500]
    last_marker_end = -1
    for marker in sidebar_markers:
        idx = sidebar_window.rfind(marker)
        if idx >= 0:
            last_marker_end = max(last_marker_end, idx + len(marker))
    if last_marker_end > 0:
        # Find first substantial line after the last marker
        tail = body[last_marker_end:]
        content_start = -1
        offset = 0
        for line in tail.split("\n"):
            line_stripped = line.strip()
            if (len(line_stripped) >= 80
                    and re.search(r"[.!?]", line_stripped)
                    and not line_stripped.startswith(("http", "#", "!", "-", "*"))):
                content_start = offset
                break
            offset += len(line) + 1  # +1 for the newline
        if content_start >= 0:
            body = tail[content_start:].lstrip("\n ")
            leading = True  # signal that we did strip something
    # Drop any leading "View X's profile" navigation line — the post
    # author's profile-link rendered as a text repeat. Both branches
    # above can leave this line at the start of the cleaned body
    # (Branch A: rendered after the `## Feed post` marker; Branch B:
    # rendered after Connect, surviving the Connect-cut). Gated on
    # `leading` so non-LinkedIn clips that happen to contain a similar
    # phrase pass through untouched (matches the conservative principle
    # described above).
    # (0.10.6 fix: previously only Branch A applied this — capture-deep
    # clips like the Praneeta D. post leaked the line into wiki body.)
    if leading:
        body = _LINKEDIN_PROFILE_NAV_LINE.sub("", body, count=1).lstrip("\n ")
    trailing = _LINKEDIN_TRAILING_MARKER.search(body)
    if trailing:
        body = body[:trailing.start()].rstrip()

    # Re-attach any post images that the trailing strip would have eaten.
    # Dedup against any images that survived the strip (no double-include).
    if post_images:
        surviving = set(_LINKEDIN_POST_IMAGE.findall(body))
        to_attach = [img for img in post_images if img not in surviving]
        if to_attach:
            body = body.rstrip() + "\n\n" + "\n\n".join(to_attach)

    if not (leading or trailing) and not post_images:
        return body  # neither marker found, no images extracted — pass unchanged
    return body.strip() + "\n"


def process_clip(clip_path: str | Path, vault_root: str | Path) -> Path:
    """Process one clip file and return the path of the written raw."""
    clip = Path(clip_path)
    if not clip.is_file():
        raise ProcessClipError(f"clip not found: {clip_path}")

    fm, body = read_raw_frontmatter(clip)
    if not fm:
        raise ProcessClipError(
            f"clip has no parseable frontmatter: {clip.name}"
        )

    title = fm.get("title", "").strip() if isinstance(fm.get("title"), str) else ""
    url = fm.get("source", "").strip() if isinstance(fm.get("source"), str) else ""

    if not title:
        # Web Clipper sometimes omits title; fall back to filename stem.
        title = clip.stem
    if not url:
        raise ProcessClipError(
            f"clip has no source URL: {clip.name} — Web Clipper output should "
            f"include `source:` in frontmatter; check the extension config"
        )

    canonical = canonicalize(url).url
    kind = source_kind(canonical)
    category = _KIND_TO_CATEGORY.get(kind, "webpage")

    # Auto-promote known-broken-via-Web-Clipper domains (LinkedIn today)
    # to capture-deep BEFORE doing any of our own write logic. Web Clipper
    # consistently captures LinkedIn posts in URN-only URL form with no
    # image refs and generic titles; capture-deep against the same URL
    # produces the rich version (full body, all carousel images, real
    # title with author attribution).
    # If capture-deep succeeds: we delegate to its output (recursively
    # process the new clip) AND delete the Web Clipper trigger clip.
    # If capture-deep fails: fall through to the regular Web Clipper
    # write path — better to land a thin raw than to fail entirely.
    if _should_promote_to_playwright(canonical, fm):
        deep_clip = _run_capture_deep(canonical, Path(vault_root))
        if deep_clip is not None:
            # Process the capture-deep clip in place of the Web Clipper one.
            # Recursive call is safe: the new clip's clipped_via field is
            # "deep-capture", so _should_promote_to_playwright returns False.
            try:
                raw_path = process_clip(deep_clip, vault_root)
            except ProcessClipError:
                raise  # let it propagate — no fallback if capture-deep
                # produced a clip but it can't be processed
            # Remove the Web Clipper trigger clip — capture-deep replaced it.
            try:
                clip.unlink()
            except OSError:
                pass  # best-effort cleanup; non-fatal
            return raw_path
        # capture-deep failed — fall through to Web Clipper write below.
        # The 0.10.13 raw_writer guards (degraded-fetch markers, 2x ratio)
        # protect any existing rich raw at this URL from being clobbered.

    # If the URL is promote-eligible but auth wasn't set up, surface the
    # one-time setup action so the user knows they're getting a thin
    # capture (Web Clipper) when they could have a rich one (capture-deep).
    _warn_if_setup_needed(canonical, fm, clip.name)

    # Body must include the actual page content. Web Clipper concatenates
    # everything below the frontmatter into the body — pass it through.
    if not body.strip():
        raise ProcessClipError(f"clip body is empty: {clip.name}")

    # Per-host chrome stripping. Today: LinkedIn only (the worst offender
    # since logged-in chrome leaks the user's own profile + premium upsells
    # into every raw). Add other hosts as their chrome shapes get characterized.
    if "linkedin.com" in canonical:
        body = _strip_linkedin_chrome(body)
        if not body.strip():
            raise ProcessClipError(
                f"clip body is empty after LinkedIn chrome strip: {clip.name} "
                f"(no `## Feed post` marker found, or post body is empty between "
                f"feed marker and reactions section)"
            )

    # Reject collision-bait clip titles. Web Clipper extracts the page's
    # `<title>` verbatim, but LinkedIn/X serve a chrome-stripped title for
    # every post (e.g. "Post | LinkedIn"). Without this fallback, every
    # raw lands with the same generic `title:` field — bad for Local Copy
    # display + Dataview tables. Derive from body's first sentence instead.
    # Bug surfaced 0.9.13 (Praneeta raw still showed "Post | LinkedIn").
    if _is_bait_title(title):
        derived = _derive_title_from_body(body)
        if derived:
            title = derived

    # Twitter/X image-size cleanup. Always safe (no-op on bodies without
    # pbs.twimg.com URLs). See _rewrite_twimg_images for rationale.
    body = _rewrite_twimg_images(body)

    # Strip broken <video blob:...> elements (X.com etc.) that render as
    # empty absolute-positioned overlays. Replaces with a "Watch on
    # source" link to the original page where the video actually plays.
    body = _strip_blob_videos(body, source_url=canonical)

    # Strip trailing `…` (U+2026) from URLs in body text. X.com (and any
    # site that visually truncates long URLs) shows `https://github.com/
    # owner/repo…` while the actual HREF points at the full URL. Web
    # Clipper / Turndown often captures the display text and loses the
    # HREF, leaving `…` in the URL. Clicking it from Obsidian then URL-
    # encodes `…` as `%E2%80%A6` and 404s — even when the underlying
    # path is real. Strip the `…` so the local-copy URL is clickable.
    # If the truncation chopped real characters, the URL still 404s, but
    # at least it lands on a normal GitHub/whatever error page the user
    # can diagnose, not a malformed-URL browser error.
    body = re.sub(r'(https?://[^\s]*?)…', r'\1', body)

    try:
        # Preserve provenance: pass through the clipped_via from the
        # source clip rather than hardcoding "web-clipper". This matters
        # for capture-deep clips (which arrive with clipped_via:
        # "deep-capture") — losing that provenance was hiding which
        # captures had the richer Playwright path vs. the thin Web
        # Clipper path. 0.10.17 fix.
        source_clipped_via = (fm.get("clipped_via") or "web-clipper").strip()
        raw_path = write_raw(
            vault_root=vault_root,
            source_type=category,
            url=canonical,
            title=title,
            body=body,
            extra={"clipped_via": source_clipped_via},
            canonicalize_url=False,  # already done above
        )
    except RawWriterError as exc:
        raise ProcessClipError(f"write failed for {clip.name}: {exc}") from exc

    # 1.1: re-clip last_updated bump.
    # If the URL already has a wiki page (silent-dedup case), touch its
    # last_updated field to today so the page bubbles up to the current
    # day's section in the All Pages dashboard. Without this, a re-clip
    # is invisible — the wiki page stays under its first-capture date
    # and the user thinks the clip didn't land.
    try:
        _touch_wiki_last_updated_for_url(vault_root, canonical)
    except Exception as exc:
        # Non-fatal: re-clip data is already on disk; the touch is just
        # for dashboard freshness.
        print(f"[process_clip] couldn't touch last_updated for {canonical}: {exc}", file=sys.stderr)
    return raw_path


def _touch_wiki_last_updated_for_url(vault_root, url):
    """If a wiki page exists for `url`, set its `last_updated:` field to
    today's date. Used after a re-clip to surface the updated capture
    in date-grouped dashboards. No-op if no wiki page exists, or if the
    field is already today's date."""
    today = time.strftime("%Y-%m-%d")
    vault_root = Path(vault_root) if not isinstance(vault_root, Path) else vault_root

    # URL → page_name via TSV lookup
    tsv_path = vault_root / "inbox" / "url-resolved.tsv"
    if not tsv_path.exists():
        return
    page_name = None
    url_lc = url.lower().rstrip('/')
    try:
        with open(tsv_path, "r", encoding="utf-8") as f:
            for line in f:
                parts = line.split("\t")
                if len(parts) < 3:
                    continue
                status = parts[0].strip()
                if status != "captured":
                    continue
                row_url = parts[2].strip().lower().rstrip('/')
                if row_url == url_lc:
                    page_name = parts[1].strip()
                    break
    except (IOError, UnicodeDecodeError):
        return
    if not page_name:
        return

    # Find the wiki file under any format subdir
    for subdir in ("webpages", "repos", "papers", "videos", "images"):
        fp = vault_root / "wiki" / "format" / subdir / f"{page_name}.md"
        if fp.exists():
            wiki_path = fp
            break
    else:
        return

    # Rewrite last_updated to today (skip if already today)
    try:
        content = wiki_path.read_text(encoding="utf-8")
    except (IOError, UnicodeDecodeError):
        return
    m = re.search(r'^(last_updated:\s*)(\d{4}-\d{2}-\d{2})', content, re.MULTILINE)
    if not m:
        # No last_updated field — insert one after date_added if present.
        m2 = re.search(r'^(date_added:\s*\d{4}-\d{2}-\d{2}\s*)$', content, re.MULTILINE)
        if m2:
            content = content[:m2.end()] + f"\nlast_updated: {today}" + content[m2.end():]
        else:
            return
    elif m.group(2) == today:
        return  # already today; nothing to do
    else:
        content = content[:m.start()] + m.group(1) + today + content[m.end():]
    try:
        wiki_path.write_text(content, encoding="utf-8")
    except IOError:
        pass


def _cli():
    """CLI entry: `python3 process_clip.py <vault> <clip-path>`."""
    if len(sys.argv) < 3:
        print("Usage: process_clip.py <vault_root> <clip_path>", file=sys.stderr)
        return 2
    try:
        path = process_clip(sys.argv[2], sys.argv[1])
    except ProcessClipError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(path)
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
