"""Shared wiki page builder for Athena.

Single source of truth for: pre-processing, naming conventions, page template,
duplicate detection, and tracking. Called by both bin/kb (shell) and the
Obsidian plugin (via spawn).

Usage from shell:
    python3 bin/lib/wiki_page.py --vault /path --raw-path raw/webpages/slug.md
    python3 bin/lib/wiki_page.py --vault /path --raw-path raw/webpages/slug.md --llm-json '{"title":...}'

Usage from plugin (stdin JSON):
    echo '{"vault":"/path","raw_path":"...","llm_result":{...}}' | python3 bin/lib/wiki_page.py --stdin

Output: JSON to stdout with { status, page_name, summary, wiki_path }
"""

import sys
import os
import re
import json
import time
import glob

# Favicon cache is best-effort — wiki pages render fine without it.
# Import path-proof against running from different working directories.
_favicons = None
try:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import favicons as _favicons
except ImportError:
    pass


# ── Pre-processor ────────────────────────────────────────────────

# Windows-reserved filename characters: the NTFS / FAT32 file-system
# reserves these for path syntax (`/`, `\`), redirection (`<`, `>`, `|`),
# wildcards (`?`, `*`), quoting (`"`), and — most painfully for Athena —
# alternate data streams (`:`). Athena's naming convention uses `:` very
# liberally (e.g. "X: ...", "Web: ...", "GitHub: ..."), which works on
# macOS HFS+/APFS and Linux ext4 but blows up on Windows with
# OSError [WinError 87] "The parameter is incorrect" on os.replace().
#
# We sanitize per-platform: POSIX vaults keep titles as-is (no change to
# the macOS/Linux file tree), Windows vaults get `:` → ` —` substitution
# and other reserved chars dropped. Frontmatter `title:` keeps the
# original — sanitization is filename-only, so wikilinks resolve to the
# same display via Obsidian's [[stem|alias]] form.
#
# Trade-off: a vault synced across macOS and Windows would have two
# different filenames for the same source. Acceptable today — full
# cross-platform vault syncing is out of scope for 1.0; documented as
# a known limitation. A migration tool to rename `:` → ` —` everywhere
# (so macOS vaults adopt the Windows-safe form) is tracked for 1.1.
_WIN_RESERVED_FILENAME_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_UNICODE_REPLACEMENT_CHAR = '�'

def _safe_filename(name):
    """Return a filename component that is valid on the current platform.

    On POSIX: returns `name` unchanged (no constraint beyond the few
    chars POSIX itself reserves, which Athena's naming convention
    already avoids). On Windows: replaces `:` with ` —` and drops any
    other reserved characters. The Unicode replacement character (the
    `�` that surfaces when text was decoded with the wrong codec) is
    dropped on all platforms — it's never user-intent and usually
    indicates upstream encoding damage.
    """
    if not isinstance(name, str):
        return name
    cleaned = name.replace(_UNICODE_REPLACEMENT_CHAR, '')
    if os.name != 'nt':
        return cleaned
    # On Windows: `:` becomes ` —` (em-dash with leading space) so the
    # title still reads naturally. Other reserved chars are dropped (no
    # great substitute exists for `<`, `>`, `|`, `?`, `*`, `"`).
    cleaned = cleaned.replace(':', ' —')
    cleaned = _WIN_RESERVED_FILENAME_RE.sub('', cleaned)
    # Trailing dots/spaces are illegal in Windows filenames (the API
    # silently strips them, which would defeat our atomic-tmp-rename
    # pattern). Trim defensively.
    cleaned = cleaned.rstrip(' .')
    # Final guard: empty after sanitization → fallback so we never
    # produce a `.md` zero-stem path.
    return cleaned or 'Untitled'


def _invalidate_regen_state(vault_path, wiki_path):
    """Remove this wiki path from scripts/.bulk-regen-state.json so the
    next bulk-llm-regen-summaries pass re-generates an Opus summary.

    Called after every wiki page write (create or overwrite). Best-effort:
    if the state file doesn't exist, isn't JSON, or any I/O fails, silently
    skip — invalidation is a quality enhancement, not a correctness
    requirement. The regen script handles missing-state gracefully on its
    end too.
    """
    state_file = os.path.join(vault_path, 'scripts', '.bulk-regen-state.json')
    if not os.path.exists(state_file):
        return
    rel = os.path.relpath(wiki_path, vault_path)
    try:
        with open(state_file, 'r', encoding='utf-8') as f:
            state = json.load(f)
        if not isinstance(state, dict):
            return
        completed = state.get('completed')
        if not isinstance(completed, list) or rel not in completed:
            return
        state['completed'] = [p for p in completed if p != rel]
        with open(state_file, 'w', encoding='utf-8') as f:
            json.dump(state, f)
    except (OSError, json.JSONDecodeError):
        pass  # never let state invalidation block the wiki write


def _clean_linkedin_content(body):
    """Strip LinkedIn-specific navigation, profile sidebar, and premium upsell junk.

    Web Clipper captures the full page including the logged-in user's sidebar,
    premium promotion banners, and nav elements. The actual post starts after
    '## Feed post' or the author's name block.

    Two-stage clean:
      1. Line-by-line leading-chrome scrub (existing — works for raws where
         leading chrome is interleaved with markdown without a clean marker)
      2. Marker-based trailing-chrome strip (added 0.9.9 — covers Jim
         Libby-style footers: 'Enjoy this? Repost', 'To view or add a comment',
         'Like/Comment/Share' action row, 'More from this author', etc.)
    """
    lines = body.split('\n')
    clean = []
    in_junk = True  # start assuming junk until we find post content

    for i, line in enumerate(lines):
        stripped = line.strip()
        # Skip obvious LinkedIn junk
        if any(pat in stripped for pat in [
            'premium/redeem', 'Get 14x more', 'Try 2 months',
            'premium_homepage', 'profile-framedphoto', '/in/xuchong',
            'upsellOrderOrigin', 'linkedin.com/premium',
            'Open to work', 'People also viewed', 'More from this author',
            'Sign in', 'Join now', 'linkedin.com/safety/go',
        ]):
            continue
        # Skip profile image lines (markdown images linking to /in/ profiles)
        if re.match(r'^\[?\s*!\[.*\]\(https://media\.licdn\.com/', stripped):
            continue
        # Skip empty link-only lines pointing to profiles
        if re.match(r'^\]\(https://www\.linkedin\.com/in/', stripped):
            continue
        # Detect start of actual post content
        if in_junk:
            # "## Feed post" marks the start of the post in LinkedIn clips
            if stripped.startswith('## Feed post') or stripped.startswith('## Post'):
                in_junk = False
                continue
            # Or: a substantial text line (not a link, not nav) after the junk
            if len(stripped) > 40 and not stripped.startswith('[') and not stripped.startswith('!'):
                in_junk = False
                clean.append(line)
                continue
            # Skip everything before post content
            continue
        # Strip LinkedIn tracking URLs wrapping regular text
        line = re.sub(r'\[([^\]]+)\]\(https://www\.linkedin\.com/safety/go/\?url=[^)]+\)', r'\1', line)
        clean.append(line)

    body = '\n'.join(clean).strip()

    # Stage 2: marker-based trailing-chrome strip — catches the post-content
    # footer that the line-by-line scrub above doesn't reliably handle.
    # Sourced from process_clip so the same regex is the single source of
    # truth for "what counts as LinkedIn trailing chrome." See
    # process_clip._LINKEDIN_TRAILING_MARKER for the full marker list.
    try:
        from process_clip import _LINKEDIN_TRAILING_MARKER  # type: ignore
        m = _LINKEDIN_TRAILING_MARKER.search(body)
        if m:
            body = body[:m.start()].rstrip()
    except Exception:
        pass  # graceful: chrome leak is preferable to wiki-page crash

    return body


def _clean_social_artifacts(body):
    """Strip common social media UI artifacts from any platform."""
    # Remove "See new posts", "Article", "Conversation" boilerplate
    lines = body.split('\n')
    clean = []
    for line in lines:
        stripped = line.strip()
        if stripped in ('Article', 'See new posts', 'Conversation', 'Show more',
                        'Show less', 'Like', 'Comment', 'Repost', 'Send',
                        'Copy link', 'Report this post'):
            continue
        # Skip follower/connection count lines
        if re.match(r'^\d+[,.]?\d*[KMkm]?\s*(followers?|connections?|posts?|likes?|comments?|reposts?)\s*$', stripped):
            continue
        clean.append(line)
    return '\n'.join(clean)


def preprocess_content(raw_text):
    """Strip Web Clipper YAML frontmatter, extract metadata, return clean body.

    Web Clipper wraps content in ---\\n...\\n---\\n YAML.
    Returns { body, url, title, description }.
    """
    if not raw_text:
        return {'body': '', 'url': None, 'title': None, 'description': None}

    body = raw_text
    url = None
    title = None
    description = None

    # Detect and strip YAML frontmatter (--- ... ---)
    yaml_match = re.match(r'^---\n(.*?)\n---\n?(.*)', raw_text, re.DOTALL)
    if yaml_match:
        yaml_block = yaml_match.group(1)
        body = yaml_match.group(2).strip()

        # Extract structured fields from YAML
        url_match = re.search(r'^source:\s*[\'"]?(https?://\S+?)[\'"]?\s*$', yaml_block, re.MULTILINE) or \
                    re.search(r'^url:\s*[\'"]?(https?://\S+?)[\'"]?\s*$', yaml_block, re.MULTILINE)
        if url_match:
            url = url_match.group(1)

        title_match = re.search(r'^title:\s*[\'"]?(.+?)[\'"]?\s*$', yaml_block, re.MULTILINE)
        if title_match:
            title = title_match.group(1).strip()

        desc_match = re.search(r'^description:\s*[\'"]?(.+?)[\'"]?\s*$', yaml_block, re.MULTILINE)
        if desc_match:
            description = desc_match.group(1).strip().strip('"')

    # Strip LinkedIn navigation/profile junk from Web Clipper captures
    if url and 'linkedin.com' in url.lower() or 'linkedin.com' in body[:200].lower():
        body = _clean_linkedin_content(body)

    # Strip common social media UI artifacts
    body = _clean_social_artifacts(body)

    # Also extract URL from **URL:** markdown pattern (non-clip raw files)
    if not url:
        md_url = re.search(r'\*\*URL:\*\*\s*(https?://\S+)', body)
        if md_url:
            url = md_url.group(1)

    # Also extract title from first heading if not found in YAML
    if not title:
        heading = re.search(r'^#\s+(.+)$', body, re.MULTILINE)
        if heading:
            title = heading.group(1).strip()

    return {'body': body, 'url': url, 'title': title, 'description': description}


# ── Naming conventions ───────────────────────────────────────────

def read_naming_rules(vault_path):
    """Read naming conventions from RULES.md. Returns the rules text or None."""
    rules_path = os.path.join(vault_path, 'RULES.md')
    try:
        with open(rules_path, 'r', encoding='utf-8') as f:
            content = f.read()
        match = re.search(r'## Naming Convention\n[\s\S]*?\n([\s\S]*?)(?=\n## |\n---|\Z)', content)
        return match.group(1).strip() if match else None
    except (IOError, OSError):
        return None


def read_tagging_rules(vault_path):
    """Read tagging rules from RULES.md. Returns the rules text or None."""
    rules_path = os.path.join(vault_path, 'RULES.md')
    try:
        with open(rules_path, 'r', encoding='utf-8') as f:
            content = f.read()
        match = re.search(r'## Tagging Rules\n[\s\S]*?\n([\s\S]*?)(?=\n## |\n---|\Z)', content)
        return match.group(1).strip() if match else None
    except (IOError, OSError):
        return None


def _match_conference_prefix(url, naming_config):
    """Return (prefix, body_strip_pattern) if the URL matches a known
    conference taxonomy, else (None, None).

    Conference patterns are config-driven (naming.conference_url_patterns
    in athena.default.json). Each entry has a host regex and a path regex
    with capture groups; the prefix template substitutes $1, $2, etc.
    body_strip_pattern (optional) is applied to the title body to remove
    the redundant leading conference identifier left by the slug-humanizer
    (e.g. 'Us 17 Robbins...' → 'Robbins...').

    Higher-priority than file_formats — lets `blackhat.com/docs/us-17/`
    become 'BlackHat USA 2017' instead of generic 'PDF'.
    """
    if not url:
        return None, None
    patterns = naming_config.get('conference_url_patterns') or []
    for entry in patterns:
        host_pat = entry.get('host_pattern')
        path_pat = entry.get('path_pattern')
        template = entry.get('prefix_template')
        if not (host_pat and path_pat and template):
            continue
        if not re.search(host_pat, url, re.IGNORECASE):
            continue
        m = re.search(path_pat, url, re.IGNORECASE)
        if not m:
            continue
        prefix = template
        for i, grp in enumerate(m.groups(), start=1):
            prefix = prefix.replace(f'${i}', grp.upper() if grp else '')
        return prefix.strip(), entry.get('body_strip_pattern')
    return None, None


def _restore_acronyms(text, acronyms):
    """Walk space-separated tokens and restore known acronyms to uppercase.
    Token comparison is case-insensitive; only tokens whose lowercase form
    matches an acronym (also lowercased) get rewritten. Preserves position
    and punctuation, so 'Active Directory Dacl Backdoors' becomes 'Active
    Directory DACL Backdoors' without touching 'Active' or 'Backdoors'.
    """
    if not text or not acronyms:
        return text
    acronym_map = {a.lower(): a for a in acronyms}

    def _replace(token):
        # Strip surrounding punctuation for comparison, then restore.
        m = re.match(r'^(\W*)(\w+)(\W*)$', token, re.UNICODE)
        if not m:
            return token
        lead, word, trail = m.groups()
        canonical = acronym_map.get(word.lower())
        return f'{lead}{canonical}{trail}' if canonical else token

    return ' '.join(_replace(t) for t in text.split(' '))


def apply_naming_convention(title, url, source_type, truncate=True):
    """Apply naming conventions from RULES.md.

    Priority order for the source prefix:
      1. Known platform (X, GitHub, LinkedIn, YouTube, arXiv) — platform name
      2. Conference URL taxonomy (BlackHat, USENIX, NDSS, IEEE S&P) —
         conference name + year. Beats file_formats below because a paper
         from a known conference is more recognizable as 'BlackHat USA 2017'
         than as generic 'PDF'.
      3. File format (PDF, Word, Excel, PowerPoint, etc.) — format name
      4. No prefix — descriptive title only

    Separator is ':' (colon), chosen over em-dash because it's one char
    shorter in column-constrained list views.

    - X posts:         "X: <topic>" (no @username)
    - GitHub repos:    "GitHub: <repo> — <description>" (no owner)
    - LinkedIn:        "LinkedIn: <topic>"
    - arXiv papers:    "arXiv: <title>"
    - YouTube videos:  "YouTube: <title>"
    - Conference PDFs: "BlackHat USA 2017: <title>" / "USENIX security22: ..."
    - Raw PDF etc.:    "PDF: <title>" / "Word: <title>" / ...

    `truncate=True` (default) caps long titles at naming.filename_max_chars
    (default 100, see athena.default.json) on a word boundary — used during
    fresh ingest. Linters pass `truncate=False` so existing
    (possibly-long-but-valid) titles don't silently get shortened on
    re-validation.
    """
    url = url or ''
    # Platform patterns sourced from bin/config/athena.json — single source of
    # truth for URL classification. See naming.platforms and naming.cloud_storage.
    from config import naming as _naming
    _n = _naming()
    def _match(key, where):
        entry = _n.get(where, {}).get(key)
        return bool(entry and re.search(entry['pattern'], url, re.IGNORECASE))
    is_tweet         = _match('x', 'platforms')
    is_repo          = _match('github', 'platforms')
    is_linkedin      = _match('linkedin', 'platforms')
    is_youtube       = _match('youtube', 'platforms')
    is_arxiv         = _match('arxiv', 'platforms')
    is_google_search = _match('google_search', 'platforms')
    is_drive         = _match('drive', 'cloud_storage')
    is_onedrive      = _match('onedrive', 'cloud_storage')
    is_dropbox       = _match('dropbox', 'cloud_storage')

    # Repair PDF line-break hyphenation: "Be- come" → "Become", "Engi- neer" →
    # "Engineer". Only joins when a lowercase letter follows the space, so real
    # compound modifiers like "state- of- the- art" with deliberate spacing
    # still round-trip (we'd want those as "state-of-the-art" too — separate concern).
    title = re.sub(r'([A-Za-z])-\s+([a-z])', r'\1\2', title)
    # Collapse any resulting double spaces.
    title = re.sub(r'\s{2,}', ' ', title).strip()

    # Strip any legacy prefix before re-applying — keeps re-ingestion idempotent.
    # Prefix list comes from naming.recognized_prefixes in the config. Sort
    # by length descending so 'BlackHat USA' is tried before 'BlackHat'
    # (regex alternation is leftmost-match in Python's re).
    _sorted_prefixes = sorted(_n['recognized_prefixes'] + ['Git'], key=len, reverse=True)
    _prefix_alt = '|'.join(re.escape(p) for p in _sorted_prefixes)
    # Optional `\s+\d{4}` allows stripping legacy year-tagged prefixes like
    # 'BlackHat 2017: ...' or 'USENIX 2024: ...' before the canonical form
    # ('BlackHat USA 2017: ...') is re-applied below.
    title = re.sub(
        rf'^({_prefix_alt})(?:\s+\d{{4}})?\s*[—:]\s*',
        '', title, flags=re.IGNORECASE).strip()

    _P = _n['platforms']
    if is_tweet:
        title = re.sub(r'@\w+', '', title).strip()
        title = re.sub(r'^(Tweet|X Post)\s*[—\-:]?\s*', '', title, flags=re.IGNORECASE).strip()
        # Clean up the double-space residue from stripping `@username` in the
        # middle of phrases like "Post by @BrianLaManna_ on X" → "Post by  on X".
        title = re.sub(r'\s{2,}', ' ', title).strip()
        # If stripping the @username left only OG-template boilerplate, replace
        # with a neutral placeholder so the user notices and can rename. The
        # body-scan fallback in build_fallback_data should catch most of these
        # earlier via the GENERIC_TITLE_PATTERNS check, but defend in depth.
        if re.fullmatch(r'Post by\s*on X\.?|on X', title, re.IGNORECASE):
            title = '(see post)'
        title = f"{_P['x']['prefix']}: " + title
    elif is_repo:
        repo_match = re.search(r'github\.com/[^/]+/([^/?#]+)', url, re.IGNORECASE)
        repo_name = repo_match.group(1) if repo_match else ''
        if repo_name:
            desc = re.sub(repo_name, '', title, flags=re.IGNORECASE).strip()
            desc = re.sub(r'^[\s—\-:]+', '', desc).strip()
            gh = _P['github']['prefix']
            title = f'{gh}: {repo_name} — {desc}' if desc else f'{gh}: {repo_name}'
    elif is_linkedin:
        title = f"{_P['linkedin']['prefix']}: " + title
    elif is_youtube:
        title = f"{_P['youtube']['prefix']}: " + title
    elif is_arxiv:
        title = f"{_P['arxiv']['prefix']}: " + title
    elif is_google_search:
        title = f"{_P['google_search']['prefix']}: " + title
    else:
        # Restore known acronyms in the descriptive part — `_humanize` ran
        # on a lowercased slug so 'ACE' is now 'Ace', 'DACL' now 'Dacl'.
        # Acronym list lives in naming.acronyms (athena.default.json).
        title = _restore_acronyms(title, _n.get('acronyms') or [])

        # Prefix priority: conference taxonomy > file_format > cloud_storage > Web.
        # Conference taxonomy wins because 'BlackHat USA 2017' is more useful
        # than generic 'PDF' for a paper from a known conference.
        conf_prefix, body_strip = _match_conference_prefix(url, _n)
        ext_match = re.search(r'\.([a-z0-9]+)(?:\?|$)', url, re.IGNORECASE)
        label = _n['file_formats'].get(ext_match.group(1).lower()) if ext_match else None
        if conf_prefix:
            if body_strip:
                title = re.sub(body_strip, '', title, count=1).strip()
            title = f'{conf_prefix}: ' + title
        elif label:
            title = f'{label}: ' + title
        elif is_drive:
            title = f"{_n['cloud_storage']['drive']['prefix']}: " + title
        elif is_onedrive:
            title = f"{_n['cloud_storage']['onedrive']['prefix']}: " + title
        elif is_dropbox:
            title = f"{_n['cloud_storage']['dropbox']['prefix']}: " + title
        elif url:
            title = f"{_n['fallback_prefix']}: " + title

    # Clean filename-unsafe chars. Note: colon is intentionally allowed
    # here because it's our source-prefix separator (X: topic, GitHub:
    # repo, PDF: title). macOS/Linux/modern Windows all accept colon
    # in filenames; Obsidian's file tree and wikilinks handle it fine.
    title = re.sub(r'[/*?"<>|]', '', title)
    title = re.sub(r'\s+', ' ', title).strip()
    if truncate:
        # Cap from naming.filename_max_chars (default 100). Cuts at the last
        # word boundary within the cap so we don't slice mid-word — that
        # produced the 'Director' artifact (issue #125). Linters pass
        # truncate=False so valid-but-long existing titles aren't shortened
        # on re-validation.
        max_chars = _n.get('filename_max_chars', 130)
        if len(title) > max_chars:
            title = title[:max_chars].rsplit(' ', 1)[0] if ' ' in title[:max_chars] else title[:max_chars]
            # Strip trailing punctuation so a mid-clause cut doesn't leave
            # a dangling `,` or `;` at end of filename (0.11.4 fix).
            title = title.rstrip(' ,;:—-.')
    # Use em dash not hyphen
    title = re.sub(r'\s-\s', ' — ', title)

    return title


# ── Fallback page data (no LLM) ─────────────────────────────────

_RAW_ASSET_PATH_RE = re.compile(r'(\]\()\.\./\.\./assets/')


# Markdown image regex — allows `]` inside alt text (PDF OCR alt text
# commonly contains `[cs.AI]` and similar). Mirrors asset_download.IMG_RE.
_BODY_IMG_RE = re.compile(
    r'!\[((?:[^\]]|\](?!\())*)\]\(([^)\s]+)(?:\s+"[^"]*")?\)'
)


def _truncate_body_preserving_images(body: str, limit: int) -> str:
    """Truncate body to `limit` chars BUT preserve every image reference.

    Image refs that fall past the cut point get re-appended after the
    truncated text, deduplicated against any image refs already inside
    the truncated portion. Without this, PDF-page-carousel posts (where
    alt text is multi-KB OCR'd content) lose every image past image 1.
    Surfaced 0.10.26 on the Suwoong LinkedIn post (5 paper-page images,
    only the first survived a hard `[:5000]` cut).
    """
    if not body or len(body) <= limit:
        return body or ''
    all_images = _BODY_IMG_RE.findall(body)
    truncated = body[:limit]
    surviving = set(_BODY_IMG_RE.findall(truncated))
    to_append = [
        f"![{alt}]({url})" for alt, url in all_images
        if (alt, url) not in surviving
    ]
    # Strip any half-cut image markdown at the end of `truncated` (the
    # truncation point may land mid-image-ref leaving a broken `![alt...`).
    last_open = truncated.rfind('![')
    if last_open > 0 and '](' not in truncated[last_open:]:
        # No `](` after the last `![` — broken markdown. Cut before it.
        truncated = truncated[:last_open].rstrip()
    if to_append:
        return truncated.rstrip() + "\n\n" + "\n\n".join(to_append) + "\n"
    return truncated


def _rewrite_asset_paths_for_wiki(body: str) -> str:
    """Rewrite raw-relative asset paths to wiki-relative paths.

    Asset markdown in raws looks like `![](../../assets/<slug>/<file>)` —
    correct from `raw/<cat>/artifacts/<file>.md` (depth 3, two levels up
    lands at `raw/assets/`). When the same body string is copied verbatim
    into a wiki page at `wiki/format/<cat>/<file>.md` (also depth 3 but
    under a different root), `../../assets/` would resolve to `wiki/assets/`
    — a directory that doesn't exist. The corrected wiki-relative path is
    `../../../raw/assets/<slug>/<file>` (three levels up to vault root,
    then into raw/assets/).

    Without this rewrite, every wiki page that embeds a backfilled asset
    shows a "could not be found" error in Obsidian's preview.
    Bug surfaced 0.9.12.
    """
    if not body:
        return body
    return _RAW_ASSET_PATH_RE.sub(r'\1../../../raw/assets/', body)


def build_fallback_data(raw_content, url, source_type, hint_title=None):
    """Build page data when LLM is unavailable.

    Returns same shape as LLM output: { title, summary, tags, related, body }.
    """
    url = url or ''
    is_tweet = bool(re.search(r'x\.com|twitter\.com', url, re.IGNORECASE))

    # Title resolution priority (issue #116):
    #   1. hint_title (from caller — usually frontmatter `title:` or LLM)
    #      UNLESS it's in GENERIC_TITLES, in which case fall through.
    #   2. body H1 — but only if not in GENERIC_TITLES.
    #   3. Body content scan for the first plausible non-metadata line.
    #      Critically, exclude lines that look like YAML frontmatter pairs
    #      (`source: ...`, `url: ...`) — the synth was picking those when
    #      raws still had inline frontmatter, producing mangled filenames
    #      like 'X: source: https:x.compseudo_sid26status...'.
    #   4. Humanized slug from the URL — safer than body-scrape garbage.
    #   5. 'Untitled' as a last resort.
    # Generic / chrome-stripped page titles that should NEVER become the wiki
    # page name. When a hint_title matches, we fall through to body-scan and
    # URL-derived fallbacks so the page gets a meaningful name.
    # The LinkedIn variants ('post | linkedin', 'feed | linkedin', etc.) were
    # added in 0.9.9 after the user's VC/Series A post landed with title
    # "LinkedIn: Post LinkedIn" — every LinkedIn page has the same chrome
    # title, so this is collision-bait at the title level (analogous to
    # _COLLISION_BAIT_SLUGS at the slug level).
    GENERIC_TITLES = {
        'tweet', 'untitled', 'untitled page', 'post', 'page',
        'browser capture', 'repository', 'x post', 'article',
        'post | linkedin', 'feed | linkedin', 'sign up | linkedin',
        'linkedin', 'log in | linkedin', 'login | linkedin',
        'home | x', 'x', 'feed | x', 'home / x',
        # 0.12.1: LinkedIn group-post 404 chrome — symmetric to UI_NOISE_RE's
        # body-side 404 filter. Without this, a raw captured during a 404 keeps
        # the chrome title as wiki page name even after manual correction.
        'linkedin 404 — page not found', '404 — page not found',
        'page not found',
    }
    # Frontmatter-pair detector: `lowercase_with_underscores: value`. Excludes
    # YAML lines from being mistaken for content. Tight on the prefix shape
    # so legitimate prose with colons (e.g. "Step 1: introduction") still
    # passes — those start with capitalized words or numbers.
    FM_PAIR_RE = re.compile(r'^[a-z][a-z0-9_]*:\s')
    title = (hint_title or '').strip()
    # Match generic titles literally OR by structural pattern. X clips often
    # arrive with "Post by @username on X" — specific-looking but carries no
    # actual tweet content; stripping the @username (in apply_naming_convention)
    # leaves "Post by on X". Treat the whole pattern as generic so the body
    # scan picks up the real tweet text instead.
    _GENERIC_TITLE_PATTERNS = (
        re.compile(r'^post by @\w+ on x\.?$', re.IGNORECASE),
        re.compile(r'^@\w+ on x$', re.IGNORECASE),
        re.compile(r'^x post by @\w+$', re.IGNORECASE),
        # 0.10.16: "View Zhaorun Chen's profile" — LinkedIn profile-link
        # chrome that survives when the chrome-strip can't find its
        # `## Feed post` anchor. Symmetric to the process_clip-side fix.
        re.compile(r"^view\s+.+?(?:'s|’s|s)\s+profile$", re.IGNORECASE),
    )
    if title and (title.lower() in GENERIC_TITLES
                  or any(p.match(title) for p in _GENERIC_TITLE_PATTERNS)):
        title = ''
    if not title:
        heading = re.search(r'^#\s+(.+)$', raw_content, re.MULTILINE)
        if heading:
            cand = heading.group(1).strip()
            # Apply BOTH the literal-set check and the structural-pattern
            # check — H1 fallback shouldn't pick up "Post by @user on X"
            # any more than the frontmatter title should.
            if (cand.lower() not in GENERIC_TITLES
                    and not any(p.match(cand) for p in _GENERIC_TITLE_PATTERNS)):
                title = cand

    # If still no title, scan content for a better one.
    if not title:
        for line in raw_content.split('\n'):
            trimmed = line.strip()
            if FM_PAIR_RE.match(trimmed):
                continue  # YAML frontmatter pair — never a real title
            if (len(trimmed) > 15 and not trimmed.startswith('http') and
                    not trimmed.startswith('- **') and not trimmed.startswith('![') and
                    not trimmed.startswith('See new') and not trimmed.startswith('#') and
                    not trimmed.startswith('@') and trimmed.lower() not in GENERIC_TITLES and
                    trimmed not in ('Article', 'Conversation', '---')):
                # Normalize Unicode math-bold/italic typography to ASCII before
                # using as title. LinkedIn posts often use 𝗖𝗹𝗮𝘂𝗱𝗲𝗕𝗹𝗲𝗲𝗱-style
                # formatting that explodes wiki filenames. Added 0.9.13.
                import unicodedata as _ud
                decomposed = _ud.normalize("NFKD", trimmed)
                normalized = "".join(c for c in decomposed if not _ud.combining(c))
                title = normalized[:65]
                break
    if not title and url:
        # Humanized slug from URL is safer than 'Untitled' for filename
        # uniqueness — duplicates with same source_type would otherwise
        # collide on 'Untitled.md'.
        try:
            from urllib.parse import urlparse
            slug_src = urlparse(url).path.strip('/').replace('/', '-') or urlparse(url).netloc
            slug_src = re.sub(r'[._-]+', ' ', slug_src).strip()
            if slug_src:
                title = slug_src[:1].upper() + slug_src[1:]
        except Exception:  # noqa: BLE001
            pass
    if not title:
        title = 'Untitled'

    title = apply_naming_convention(title, url, source_type)

    # Summary: first paragraph of actual content. CRITICAL: strip the
    # raw's YAML frontmatter first — without this, raw files written by
    # the canonical writer (which begin with '---title:...---') leak the
    # frontmatter as the 'first paragraph' because the YAML block has
    # no \n\n inside it and survives the split. Symptom (#142): summary
    # field literally contained '--- title: "..." source: "..." ...'.
    body_for_summary = raw_content
    if body_for_summary.startswith('---'):
        end = re.search(r'\n---[\s]*\n', body_for_summary[3:])
        if end:
            body_for_summary = body_for_summary[end.end() + 3:]

    # UI-noise patterns: text that captured from a webpage's chrome
    # rather than its content. These leak into summary when the source
    # has a thin first paragraph followed by login/error/share buttons
    # (LinkedIn, X, Substack, Notion, etc.).
    UI_NOISE_RE = re.compile(
        r'^(Edit|Oops|Loading|Sign in|Log in|Share|Subscribe|Follow|'
        r'See more|Show more|Continue reading|Comments|Reply|Repost|'
        r'Skip to content|Jump to|Cookie|Accept|Privacy)\b|'
        r'(Something went wrong|please (?:try again|enable|update)|'
        r'we couldn\'t|we cannot|access denied|requires? (?:authentication|authorization|login)|access to this page requires|'
        r'404|page not found|not available)',
        re.IGNORECASE,
    )

    summary = ''
    for p in body_for_summary.split('\n\n'):
        # Skip paragraphs that look like embedded YAML frontmatter (legacy
        # raws had wiki frontmatter embedded as body content via lint's old
        # 'Missing raw files auto-create' path — see #143).
        first_lines = p.strip().split('\n', 5)[:5]
        if sum(1 for line in first_lines if re.match(r'^[a-z][a-z0-9_]*:\s', line)) >= 2:
            continue
        # Strip headers, metadata bullets, and any leftover frontmatter pairs
        cleaned = re.sub(r'^#+\s+.*$', '', p, flags=re.MULTILINE)
        cleaned = re.sub(r'^-\s+\*\*.*$', '', cleaned, flags=re.MULTILINE)
        cleaned = re.sub(r'^[a-z][a-z0-9_]*:\s.*$', '', cleaned, flags=re.MULTILINE).strip()
        if len(cleaned) <= 30:
            continue
        if cleaned.startswith(('http', '![', '---')):
            continue
        # Skip UI-chrome noise
        if UI_NOISE_RE.search(cleaned[:120]):
            continue
        # Require minimum substantive length AND a space so we don't pick
        # up single tokens
        if len(cleaned) < 50 or ' ' not in cleaned:
            continue
        summary = cleaned.replace('\n', ' ').strip()
        break
    summary = summary.replace('"', "'")

    # Tags: infer from source type and content keywords
    tags = set()
    tags.add(source_type if source_type != 'webpage' else 'webpage')
    body_lower = raw_content.lower()
    tag_map = {
        'security': ['security', 'vulnerability', 'exploit', 'pentest', 'cve', 'threat'],
        'ai-agents': ['agent', 'agentic', 'autonomous'],
        'llm': ['llm', 'language model', 'gpt', 'claude', 'transformer'],
        'ml': ['machine learning', 'neural network', 'training', 'gradient'],
        'claude-code': ['claude code', 'claude-code', 'mcp server', 'skill'],
        'memory': ['memory', 'knowledge graph', 'rag', 'retrieval'],
        'obsidian': ['obsidian', 'vault', 'wikilink'],
        'deep-learning': ['deep learning', 'attention mechanism', 'backpropagation'],
        'python': ['python', 'pip install', 'pytorch', 'tensorflow'],
    }
    for tag, keywords in tag_map.items():
        if any(kw in body_lower for kw in keywords):
            tags.add(tag)

    # Related: simple keyword match against topic pages
    related = []
    # (caller provides topic names if available)

    # Body: clean up
    body = raw_content
    body = re.sub(r'^#\s+.+$', '', body, count=1, flags=re.MULTILINE).strip()
    body = body.replace('&#8211;', '\u2014').replace('&#8212;', '\u2014').replace('&amp;', '&')

    # Truncate at 3000 chars for fallback summary, but preserve all image
    # references (0.10.26 fix \u2014 see _truncate_body_preserving_images).
    # 0.11.10: raised 3000 → 50000 (matches build_wiki_page) so the
    # fallback summary doesn't truncate before image extraction sees
    # the full body. Original 3000 was a "quick preview" assumption
    # that diverged from the wiki body cap.
    body = _truncate_body_preserving_images(body, 50000)

    return {
        'title': title,
        'summary': summary,
        'tags': sorted(tags),
        'related': related,
        'body': body,
    }


# ── Find related topics ─────────────────────────────────────────

def find_related_topics(title, vault_path):
    """Find related topic pages via keyword overlap."""
    related = []
    try:
        stop_words = {'the', 'a', 'an', 'of', 'for', 'and', 'in', 'on', 'to', 'with', 'tweet', 'post'}
        title_words = set(re.sub(r'[^\w\s]', '', title.lower()).split()) - stop_words
        if len(title_words) < 2:
            return []
        for wf in glob.glob(os.path.join(vault_path, 'wiki', 'topics', '*.md')):
            topic_name = os.path.splitext(os.path.basename(wf))[0]
            topic_words = set(topic_name.lower().split()) - stop_words
            if len(title_words & topic_words) >= 1:
                related.append(topic_name)
    except Exception:
        pass
    return related


# ── Wiki page template ───────────────────────────────────────────

# Canonical tags — must match the set in bin/kb lint
CANONICAL_TAGS = {
    'agent', 'ai-agents', 'ai-engineering', 'ai-platforms', 'book', 'chart', 'cs153',
    'claude-code', 'competitor', 'course', 'curated-list', 'dashboard',
    'deep-learning', 'devsecops', 'diagram', 'exercises', 'export', 'finance', 'insight',
    'interview-prep', 'journal', 'karpathy', 'keyword-index', 'knowledge-base',
    'knowledge-management', 'learning', 'llm', 'marketing', 'math', 'mcp', 'memory', 'ml',
    'obsidian', 'offensive-security', 'paper', 'pdf', 'platform-engineering',
    'productivity', 'profile', 'project',
    'prompt-engineering', 'python', 'rag', 'repo', 'second-brain', 'security', 'session',
    'skills', 'slides', 'tool', 'tools', 'topic', 'video', 'visualization',
    'vulnerability-research', 'webpage', 'workflow',
}

# Map common non-canonical tags to their canonical equivalents
TAG_MAP = {
    'anthropic': 'claude-code', 'claude': 'claude-code', 'openai': 'llm',
    'gpt': 'llm', 'transformer': 'llm', 'language-model': 'llm',
    'javascript': 'tools', 'typescript': 'tools', 'react': 'tools',
    'node': 'tools', 'rust': 'tools', 'golang': 'tools',
    'code-formatting': 'tools', 'developer-tools': 'tools',
    'open-source': 'repo', 'github': 'repo',
    'reference': 'learning', 'tutorial': 'learning', 'guide': 'learning',
    'automation': 'workflow', 'devops': 'devsecops',
    'nlp': 'llm', 'neural-network': 'deep-learning',
    'cybersecurity': 'security', 'pentest': 'offensive-security',
    'data-science': 'ml', 'machine-learning': 'ml',
}


def _validate_tags(tags):
    """Filter tags to canonical set, mapping common variants."""
    validated = []
    seen = set()
    for t in tags:
        t = t.strip().lower()
        # Map non-canonical to canonical
        if t in TAG_MAP:
            t = TAG_MAP[t]
        # Only keep canonical tags
        if t in CANONICAL_TAGS and t not in seen:
            validated.append(t)
            seen.add(t)
    return validated


_BINARY_SIBLING_EXTS = ('pdf', 'docx', 'doc', 'xlsx', 'xls', 'pptx', 'ppt', 'epub')


def _find_binary_sibling(vault_path, raw_path):
    """Given a raw .md path like 'raw/papers/foo.md', return a vault-
    relative path to a downloaded binary next to it (foo.pdf, foo.docx,
    ...) or None if no sibling exists. Callers use this to make the
    Local Copy wikilink point at the viewable original rather than the
    extracted-text intermediate."""
    if not raw_path or not raw_path.endswith('.md'):
        return None
    base = raw_path[:-3]  # strip '.md'
    for ext in _BINARY_SIBLING_EXTS:
        candidate = f'{base}.{ext}'
        if os.path.exists(os.path.join(vault_path, candidate)):
            return candidate
    return None


def build_wiki_page(title, summary, tags, related, body, source_type,
                    raw_path, url=None, overwrite_path=None, source_icon=None,
                    local_copy_path=None):
    """Build a wiki page string. Returns the full page content.

    This is the SINGLE source of truth for wiki page format.
    Both plugin and shell produce pages through this function.

    source_icon (optional) is either:
      - a vault-relative path to a cached favicon PNG
        (rendered as an Obsidian embed: ![[path|16]])
      - a single emoji string (rendered as text)
      - None (no icon prepended to the Source line)

    local_copy_path (optional) is the vault-relative path the "Local
    Copy" wikilink should point to. Defaults to raw_path. For PDF /
    Office ingests, callers pass the downloaded binary (nist.pdf,
    report.docx, ...) since clicking through to the extracted .md
    intermediate is less useful — users want the rendered original.
    """
    today = time.strftime('%Y-%m-%d')

    # Validate tags against canonical set — filter out non-canonical tags
    tags = _validate_tags(tags or [])
    tags_str = ', '.join(tags) if tags else ''
    # Summary cap: cut at SENTENCE BOUNDARY near 1500 chars instead of
    # hard-trimming mid-word. The previous [:200] then [:500] hard-cuts
    # produced 'organizati' / 'three hour' style mid-word truncation in
    # dashboards. With sentence-boundary cuts:
    #   - Summaries <= 1500 chars: kept verbatim.
    #   - Summaries > 1500 chars: cut at the last '. ' / '! ' / '? '
    #     boundary within the first 1500 chars, then suffixed with '…'
    #     so the truncation is honest and visually distinguishable.
    #   - Summaries with no sentence boundary in the first 1500 chars
    #     (rare — single very long sentence): hard-cut at a word
    #     boundary near 1500 with '…' suffix.
    # 1500 chars accommodates most LLM output and first-paragraph
    # extraction without making frontmatter unwieldy. The '…' marker
    # tells dashboards exactly where the cut happened.
    raw_summary = (summary or '').replace('"', "'")
    if len(raw_summary) <= 1500:
        clean_summary = raw_summary
    else:
        # Find the last sentence boundary within the first 1500 chars
        head = raw_summary[:1500]
        sentence_match = max(
            (head.rfind(p) for p in ('. ', '! ', '? ')),
            default=-1,
        )
        if sentence_match > 1000:
            # Found a sentence boundary in the back half — clean cut
            clean_summary = head[: sentence_match + 1].rstrip() + ' …'
        else:
            # No good sentence boundary; word-boundary fallback
            word_cut = head.rfind(' ')
            clean_summary = (head[:word_cut] if word_cut > 1300 else head) + '…'

    # YAML-safe quoting for double-quoted values. We hand-roll frontmatter
    # rather than pulling pyyaml (no extra deps); the cost is that we must
    # escape backslashes and double quotes ourselves. The Windows bug
    # surfaced in 1.0.11 testing: raw_path contained `\` separators
    # (`raw\webpages\artifacts\foo.md`), and `\w`/`\a` aren't valid YAML
    # escape sequences in a double-quoted scalar → Obsidian banners
    # "invalid properties". Fix: normalize all paths to forward slashes
    # (POSIX style works on every OS Python supports) AND escape `\` + `"`
    # universally as a safety net for any other value with embedded
    # backslashes.
    def _yaml_dq(v):
        if v is None:
            return ""
        return str(v).replace('\\', '\\\\').replace('"', '\\"')
    def _posix(p):
        return (p or "").replace('\\', '/')

    safe_title = _yaml_dq(title)
    safe_source_type = _yaml_dq(source_type)
    safe_raw_path = _yaml_dq(_posix(raw_path))
    page = f'---\ntitle: "{safe_title}"\nsource_type: "{safe_source_type}"\nraw_path: "{safe_raw_path}"\n'
    if url:
        page += f'url: "{_yaml_dq(url)}"\n'
    page += f'date_added: {today}\nlast_updated: {today}\ntags: [{tags_str}]\n'

    if related:
        page += 'related:\n' + '\n'.join(f'  - "[[{r}]]"' for r in related) + '\n'
    else:
        page += 'related:\n'
    page += f'summary: "{_yaml_dq(clean_summary)}"\n---\n'

    # Source + Local Copy line (same format for all entry points).
    # source_icon rendering: an Obsidian image embed when it looks like
    # a path ("raw/favicons/x.com.png"), otherwise treated as emoji text.
    # The |16 sizes the embed to match the inline text.
    if url:
        if source_icon:
            if '/' in source_icon:
                page += f'![[{source_icon}|16]] '
            else:
                page += f'{source_icon} '
        page += f'[Source]({url}) \u00B7 '
    # Wikilink paths must use forward slashes — Obsidian's wikilink
    # resolver doesn't follow backslash paths on Windows. Without this
    # normalize, the user clicks Local Copy and Obsidian offers to
    # CREATE a new note instead of opening the raw (cue the empty
    # `... 1.md`, `... 2.md`, `... N.md` Obsidian-auto-numbered stubs
    # piling up in the vault).
    local_copy = (local_copy_path or raw_path or "").replace('\\', '/')
    page += f'[[{local_copy}|Local Copy]]\n\n'

    # Structured digest placeholder — filled in by bulk-llm-regen-summaries.py.
    # Wiki pages are synthesis artifacts, not copies of the raw body. The raw
    # content (including images) lives in raw/ and is always reachable via the
    # Local Copy link above. The digest is generated asynchronously by Opus so
    # it is never stale relative to the raw source.
    page += '## Key Findings\n\n*Pending synthesis — run `kb regen` to generate.*\n\n'

    # Connections section
    if related:
        page += '\n## Connections\n\n'
        for r in related:
            page += f'- [[{r}]]\n'

    # Keywords section
    if tags:
        page += '\n## Keywords\n'
        page += ' \u00B7 '.join(f'[[{t}]]' for t in tags) + '\n'

    return page


# ── Tracking ─────────────────────────────────────────────────────

def update_tracking(url, page_name, vault_path, regenerate_dashboard=True):
    """Update url-resolved.tsv and regenerate URL Tracker dashboard."""
    if not url:
        return
    tsv_path = os.path.join(vault_path, 'inbox', 'url-resolved.tsv')
    try:
        tsv = ''
        if os.path.exists(tsv_path):
            with open(tsv_path, 'r', encoding='utf-8') as f:
                tsv = f.read()
        # Remove old entry for this URL (case-insensitive)
        url_lower = url.lower()
        lines = [l for l in tsv.split('\n') if url_lower not in l.lower()]
        ts = time.strftime('%Y-%m-%dT%H:%M:%S')
        lines.append(f'captured\t{page_name or "unknown"}\t{url}\t{ts}')
        tsv = '\n'.join(l for l in lines if l.strip())
        with open(tsv_path, 'w', encoding='utf-8') as f:
            f.write(tsv + '\n')
        # Regenerate URL Tracker dashboard so it stays in sync
        if regenerate_dashboard:
            try:
                generate_url_tracker(vault_path)
            except Exception:
                pass  # dashboard regeneration is non-critical
    except Exception as e:
        print(f'[wiki_page] tracking update failed: {e}', file=sys.stderr)


def reconcile_tracking(vault_path):
    """Reconcile url-resolved.tsv with actual wiki page names.

    Fixes: slug names, duplicate entries, stale titles.
    Matches each URL to the wiki page that references it, updating the title column.
    """
    tsv_path = os.path.join(vault_path, 'inbox', 'url-resolved.tsv')
    if not os.path.exists(tsv_path):
        return

    # Build URL → wiki page name index (scan all wiki pages once)
    url_to_page = {}
    for wd in ['wiki/format/webpages', 'wiki/format/repos', 'wiki/format/papers',
                'wiki/format/videos', 'wiki/format/images']:
        full_wd = os.path.join(vault_path, wd)
        if not os.path.isdir(full_wd):
            continue
        for wf in os.listdir(full_wd):
            if not wf.endswith('.md'):
                continue
            page_name = wf[:-3]
            try:
                with open(os.path.join(full_wd, wf), 'r', encoding='utf-8', errors='replace') as fh:
                    head = fh.read(600)
                url_match = re.search(r'^url:\s*"?(https?://\S+?)"?\s*$', head, re.MULTILINE)
                if url_match:
                    # Normalize URL for matching
                    page_url = url_match.group(1).rstrip('/')
                    page_url = re.sub(r'[?&](utm_\w+|s|t|rcm|ref|usp|si|igsh|fbclid)=[^&]*', '', page_url)
                    page_url = re.sub(r'[?&]$', '', page_url).lower()
                    url_to_page[page_url] = page_name
            except Exception:
                pass

    # Read and fix TSV entries
    with open(tsv_path, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    seen_urls = set()
    fixed_lines = []
    for line in lines:
        line = line.rstrip('\n')
        if not line or line.startswith('#') or line.startswith('status'):
            fixed_lines.append(line)
            continue
        parts = line.split('\t')
        if len(parts) < 3:
            fixed_lines.append(line)
            continue

        status, title, url = parts[0], parts[1], parts[2]
        rest = parts[3:]

        # Normalize URL for dedup
        norm_url = url.rstrip('/').lower()
        norm_url = re.sub(r'[?&](utm_\w+|s|t|rcm|ref|usp|si|igsh|fbclid)=[^&]*', '', norm_url)
        norm_url = re.sub(r'[?&]$', '', norm_url)

        # Skip duplicates (keep last occurrence)
        if norm_url in seen_urls:
            continue
        seen_urls.add(norm_url)

        # Fix title: if we have a wiki page for this URL, use its name
        if norm_url in url_to_page:
            title = url_to_page[norm_url]

        fixed_lines.append('\t'.join([status, title, url] + rest))

    with open(tsv_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(fixed_lines) + '\n')


def generate_url_tracker(vault_path):
    """Generate the URL Tracker dashboard from url-resolved.tsv."""
    tsv_path = os.path.join(vault_path, 'inbox', 'url-resolved.tsv')
    pending_path = os.path.join(vault_path, 'inbox', 'url-new.txt')
    tracker_path = os.path.join(vault_path, 'inbox', 'URL Tracker.md')

    from collections import defaultdict
    entries = defaultdict(list)
    all_ordered = []

    # TSV has two row shapes from across the project's history:
    #   4-col modern:  status \t title \t url \t ISO-timestamp
    #   5-col legacy:  status \t title \t source_url \t resolved_url \t type
    # Disambiguate by checking whether parts[3] looks like an ISO date.
    # Misclassifying these silently put resolved_urls into the date field,
    # which surfaced as URLs appearing in the "timestamp" position of the
    # new All-URLs dashboard.
    _ISO_DATE_RE = re.compile(r'^\d{4}-\d{2}-\d{2}')
    if os.path.exists(tsv_path):
        with open(tsv_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#') or line.startswith('status'):
                    continue
                parts = line.split('\t')
                if len(parts) >= 3:
                    date_str = ''
                    if len(parts) > 3 and _ISO_DATE_RE.match(parts[3] or ''):
                        date_str = parts[3]
                    entry = {
                        'title': parts[1], 'url': parts[2],
                        'date': date_str,
                        'status': parts[0],
                    }
                    entries[parts[0]].append(entry)
                    if parts[0] in ('captured', 'has-content'):
                        all_ordered.append(entry)

    if os.path.exists(pending_path):
        with open(pending_path, 'r', encoding='utf-8') as f:
            for line in f:
                url = line.strip()
                if url and url.startswith('http'):
                    entries['pending'].append({'title': '', 'url': url, 'date': '', 'status': 'pending'})

    def _clean(t, mx=65):
        if not t or t == 'Untitled':
            return t or 'Untitled'
        if len(t) <= mx:
            return t.rstrip(',;: (')
        t = t[:mx]
        if ' ' in t:
            t = t.rsplit(' ', 1)[0]
        return t.rstrip(',;: (')

    total = sum(len(v) for v in entries.values())
    page = f'> {total} URLs tracked \u00B7 Updated {time.strftime("%Y-%m-%d %H:%M")}\n\n'

    # Needs Attention
    thin = entries.get('thin', [])
    uncapturable = entries.get('uncapturable', [])
    needs_help = thin + uncapturable
    if needs_help:
        x_urls = [e for e in needs_help if 'x.com/' in e['url'] or 'twitter.com/' in e['url']]
        li_urls = [e for e in needs_help if 'linkedin.com/' in e['url']]
        other_urls = [e for e in needs_help if e not in x_urls and e not in li_urls]
        page += f'## Needs Attention ({len(needs_help)})\n\n'
        if x_urls:
            page += f'### X/Twitter ({len(x_urls)})\nClick link \u2192 opens in browser \u2192 click Web Clipper \u2192 Athena auto-ingests\n\n'
            for e in x_urls:
                page += f'- [{_clean(e["title"] or "Tweet")}]({e["url"]})\n'
            page += '\n'
        if li_urls:
            page += f'### LinkedIn ({len(li_urls)})\nClick link \u2192 opens in browser \u2192 click Web Clipper \u2192 Athena auto-ingests\n\n'
            for e in li_urls:
                page += f'- [{_clean(e["title"] or "Post")}]({e["url"]})\n'
            page += '\n'
        if other_urls:
            page += f'### Other ({len(other_urls)})\n'
            for e in other_urls:
                page += f'- [{_clean(e["title"] or e["url"][:50])}]({e["url"]})\n'
            page += '\n'

    # Pending
    pend = entries.get('pending', [])
    if pend:
        page += f'## Pending ({len(pend)})\n\nRun `kb add` to process these.\n\n'
        for e in pend:
            page += f'- {e["url"]}\n'
        page += '\n'

    # All URLs (Full History) — every entry in the TSV regardless of
    # status, newest first, with status icon + timestamp. The full audit
    # view: a user re-clipping a page that's already in the KB can see
    # the dedup happen here (status ↻) instead of wondering whether their
    # clip went through. Replaces the previous "Recently Added (last 20)"
    # section which forced users to grep disk to answer "did this work?"
    #
    # Build the full ordered list across ALL statuses (was just captured +
    # has-content). Pending entries already have their own section above,
    # so skip them here to avoid duplication.
    full_history = []
    for status, status_entries in entries.items():
        if status == 'pending':
            continue
        for e in status_entries:
            full_history.append({**e, 'status': status})
    # Sort newest first by date (ISO timestamps lexicographically sort
    # chronologically). Entries with no date go to the end.
    full_history.sort(key=lambda e: e.get('date') or '', reverse=True)

    if full_history:
        import html as _html
        # Index: URL (no trailing slash, lowercased) → wiki page basename.
        url_to_wiki_stem = {}
        for wpath in glob.glob(os.path.join(vault_path, 'wiki', '**', '*.md'), recursive=True):
            stem = os.path.splitext(os.path.basename(wpath))[0]
            if stem in ('_TEMPLATE', '_Contents'):
                continue
            try:
                with open(wpath, 'r', encoding='utf-8') as fh:
                    head = fh.read(4096)
            except (IOError, UnicodeDecodeError):
                continue
            for m in re.finditer(r'^\s*url:\s*"?([^"\n]+)"?\s*$', head, re.MULTILINE):
                u = m.group(1).strip().rstrip('/').lower()
                if u and u not in url_to_wiki_stem:
                    url_to_wiki_stem[u] = stem

        def _wikilink_for(entry):
            """Return the [[...]] body for a TSV entry, preferring the
            actual wiki filename (URL-indexed) and falling back to a
            sanitized title that won't clobber Obsidian's display-pipe."""
            url_key = (entry.get('url') or '').strip().rstrip('/').lower()
            stem = url_to_wiki_stem.get(url_key)
            if stem:
                return stem
            t = _html.unescape((entry.get('title') or '').strip())
            return t.replace('|', '—').replace('  ', ' ').strip() or 'Untitled'

        # Status icons + line formatters. Wikilinks for resolvable pages,
        # plain URLs for failures (no wiki page to point at). Failed and
        # removed entries get a tilde-strikethrough hint via plain text
        # so they read as "tried this, didn't land" at a glance.
        STATUS_DISPLAY = {
            'captured':     ('✓', 'wikilink'),
            'has-content':  ('✓', 'wikilink'),
            'thin':         ('✗', 'failed'),
            'uncapturable': ('✗', 'failed'),
            'removed':      ('⊘', 'removed'),
            'duplicate':    ('↻', 'wikilink'),  # rare in TSV today but reserved
        }

        def _format_entry(e):
            icon, mode = STATUS_DISPLAY.get(e['status'], ('•', 'wikilink'))
            ts = (e.get('date') or '').replace('T', ' ')[:16]  # YYYY-MM-DD HH:MM
            ts_part = f"{ts} — " if ts else ""
            if mode == 'wikilink':
                return f"- {icon} {ts_part}[[{_wikilink_for(e)}]]"
            if mode == 'failed':
                title = _clean(e.get('title') or e.get('url', '')[:60])
                return f"- {icon} {ts_part}~~{title}~~ ({e['status']}) — <{e['url']}>"
            if mode == 'removed':
                title = _clean(e.get('title') or 'Removed')
                return f"- {icon} {ts_part}~~{title}~~ (removed)"
            return f"- {icon} {ts_part}{e.get('title') or e.get('url')}"

        page += f'## All URLs ({len(full_history)} total)\n\n'
        page += '> ✓ captured · ↻ duplicate · ✗ failed · ⊘ removed\n\n'
        for e in full_history:
            page += _format_entry(e) + '\n'
        page += '\n'

    with open(tracker_path, 'w', encoding='utf-8') as f:
        f.write(page)

    # Regenerate the companion page-index dashboard so the two views stay
    # in sync. Failure is non-critical (one dashboard out of date is not
    # worth crashing an ingest run); errors are swallowed.
    try:
        generate_page_index(vault_path)
    except Exception as _e:
        print(f'[wiki_page] page index regen failed: {_e}', file=sys.stderr)


def generate_page_index(vault_path):
    """Generate wiki/dashboards/All Pages.md — every wiki page grouped by
    date_added, newest date first. Companion to generate_url_tracker:
    URL Tracker shows inputs (what the user submitted), this shows
    outputs (what the KB synthesized). Together they answer "what did
    I do today?" without grepping disk.

    Frontmatter expected on each wiki page:
      date_added: YYYY-MM-DD  (preferred)
      title:      "..."        (used for the [[link|display]] form so
                                the dashboard reads naturally even when
                                the filename is a long slug)
      summary:    "..."        (first sentence shown inline for context)

    Pages without date_added fall into an 'Undated' group at the bottom
    so the dashboard surfaces them rather than silently hiding them.
    """
    dashboards_dir = os.path.join(vault_path, 'wiki', 'dashboards')
    try:
        os.makedirs(dashboards_dir, exist_ok=True)
    except OSError:
        pass
    out_path = os.path.join(dashboards_dir, 'All Pages.md')

    from collections import defaultdict
    by_date = defaultdict(list)
    total = 0

    # Scan every wiki .md file. The wiki/dashboards/ output itself is
    # excluded so we don't list our own output (and we don't recurse
    # into _TEMPLATE / _Contents skeleton files).
    for wpath in glob.glob(os.path.join(vault_path, 'wiki', '**', '*.md'), recursive=True):
        stem = os.path.splitext(os.path.basename(wpath))[0]
        if stem in ('_TEMPLATE', '_Contents', 'All Pages'):
            continue
        if '/dashboards/' in wpath:
            continue
        try:
            with open(wpath, 'r', encoding='utf-8') as fh:
                head = fh.read(4096)
        except (IOError, UnicodeDecodeError):
            continue
        # Extract date_added (preferred) or fall back to last_updated /
        # date / created. None of those? File goes to 'Undated' group.
        date_str = ''
        for key in ('date_added', 'last_updated', 'date', 'created'):
            m = re.search(r'^\s*' + key + r':\s*"?(\d{4}-\d{2}-\d{2})', head, re.MULTILINE)
            if m:
                date_str = m.group(1)
                break
        # Extract title for richer display; default to stem so an
        # incomplete frontmatter still renders as a clickable wikilink.
        title_m = re.search(r'^\s*title:\s*"?([^"\n]+?)"?\s*$', head, re.MULTILINE)
        title = title_m.group(1).strip() if title_m else stem
        # First-sentence summary (cap at ~140 chars) for inline context.
        summ_m = re.search(r'^\s*summary:\s*"?([^"\n]+?)"?\s*$', head, re.MULTILINE)
        summary = summ_m.group(1).strip() if summ_m else ''
        if len(summary) > 140:
            summary = summary[:137].rsplit(' ', 1)[0] + '…'

        by_date[date_str or '__undated__'].append({
            'stem': stem,
            'title': title,
            'summary': summary,
        })
        total += 1

    # Sort dates newest first; undated bucket pinned to the end. Within
    # each date bucket, sort alphabetically by title for stable display.
    dated_keys = sorted([k for k in by_date if k != '__undated__'], reverse=True)
    ordered_keys = dated_keys + (['__undated__'] if '__undated__' in by_date else [])

    page = f'> {total} pages · Updated {time.strftime("%Y-%m-%d %H:%M")}\n\n'
    page += '# All Pages\n\n'
    page += 'Every wiki page in chronological order. Companion to [[URL Tracker]] '
    page += '(which lists all URLs the user has submitted, including duplicates and failures).\n\n'

    for date_key in ordered_keys:
        bucket = sorted(by_date[date_key], key=lambda e: e['title'].lower())
        label = date_key if date_key != '__undated__' else 'Undated'
        page += f'## {label} ({len(bucket)})\n\n'
        for e in bucket:
            line = f'- [[{e["stem"]}|{e["title"]}]]'
            if e['summary']:
                line += f' — {e["summary"]}'
            page += line + '\n'
        page += '\n'

    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(page)


# ── Journal ──────────────────────────────────────────────────────

def create_journal_entry(vault_path, text):
    """Append a timestamped journal entry. One file per day in wiki/journal/.

    Returns { status, file_path, date }.
    """
    if not text or not text.strip():
        return {'status': 'empty', 'file_path': None, 'date': None}

    today = time.strftime('%Y-%m-%d')
    now = time.strftime('%H:%M')
    journal_dir = os.path.join(vault_path, 'wiki', 'journal')
    os.makedirs(journal_dir, exist_ok=True)
    journal_file = os.path.join(journal_dir, f'Journal-{today}.md')

    # Create file with frontmatter if new
    if not os.path.exists(journal_file):
        with open(journal_file, 'w', encoding='utf-8') as f:
            f.write(f'---\ntitle: "Journal-{today}"\nsource_type: "topic"\n'
                    f'date_added: {today}\nlast_updated: {today}\n'
                    f'tags: [journal]\nrelated:\n---\n\n')

    # Append entry
    with open(journal_file, 'a', encoding='utf-8') as f:
        f.write(f'### {now}\n\n{text.strip()}\n\n---\n\n')

    update_index_counts(vault_path)
    rel_path = os.path.relpath(journal_file, vault_path)
    return {'status': 'saved', 'file_path': rel_path, 'date': today}


def list_recent_journal(vault_path, days=7):
    """List recent journal entries. Returns list of { date, count, previews }."""
    journal_dir = os.path.join(vault_path, 'wiki', 'journal')
    files = sorted(glob.glob(os.path.join(journal_dir, '*.md')), reverse=True)
    result = []
    for f in files[:days]:
        date = os.path.splitext(os.path.basename(f))[0]
        with open(f, 'r', encoding='utf-8') as fh:
            content = fh.read()
        entries = re.findall(r'### \d{2}:\d{2}\n\n(.+?)(?=\n### |\n---|\Z)', content, re.DOTALL)
        previews = [e.strip().split('\n')[0][:80] for e in entries[-3:]]
        result.append({'date': date, 'count': len(entries), 'previews': previews})
    return result


# ── Insight ──────────────────────────────────────────────────────

def create_insight_page(vault_path, title, body=None):
    """Create a permanent insight page in wiki/insights/.

    Returns { status, file_path, title }.
    """
    if not title:
        return {'status': 'error', 'file_path': None, 'title': None,
                'message': 'Title required'}

    # Safety check
    if '..' in title or '/' in title or '\\' in title:
        return {'status': 'error', 'file_path': None, 'title': title,
                'message': f'Title contains unsafe characters: {title}'}

    insight_dir = os.path.join(vault_path, 'wiki', 'insights')
    os.makedirs(insight_dir, exist_ok=True)
    out_path = os.path.join(insight_dir, f'{title}.md')

    if os.path.exists(out_path):
        return {'status': 'exists', 'file_path': os.path.relpath(out_path, vault_path),
                'title': title, 'message': f'Insight already exists: {title}'}

    today = time.strftime('%Y-%m-%d')
    body_text = body or '<!-- AI will draft this insight with citations in your AI session. -->'

    content = f'''---
title: "{title}"
source_type: "insight"
date_added: {today}
last_updated: {today}
tags: [insight]
related:
summary: ""
---

{body_text}

## Evidence

<!-- Link to wiki pages and journal entries that support this insight -->

## Open Questions

<!-- What's still unclear or needs more evidence? -->

## Keywords
[[insight]]
'''

    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(content)

    update_index_counts(vault_path)
    return {'status': 'created', 'file_path': os.path.relpath(out_path, vault_path),
            'title': title}


def list_insights(vault_path):
    """List existing insight pages. Returns list of titles."""
    insight_dir = os.path.join(vault_path, 'wiki', 'insights')
    files = sorted(glob.glob(os.path.join(insight_dir, '*.md')))
    return [os.path.splitext(os.path.basename(f))[0]
            for f in files
            if os.path.basename(f) not in ('.gitkeep', '_TEMPLATE.md')]


# ── Index count update ───────────────────────────────────────────

def update_index_counts(vault_path):
    """Update wiki/raw page counts in index.md."""
    index_path = os.path.join(vault_path, 'index.md')
    if not os.path.exists(index_path):
        return
    try:
        with open(index_path, 'r', encoding='utf-8') as f:
            idx = f.read()
        actual_wiki = len([f for f in glob.glob(os.path.join(vault_path, 'wiki', '**', '*.md'), recursive=True)
                           if '.gitkeep' not in f and '_TEMPLATE' not in f])
        actual_raw = len([f for f in glob.glob(os.path.join(vault_path, 'raw', '**', '*.md'), recursive=True)
                          if '.gitkeep' not in f and '_TEMPLATE' not in f])
        idx = re.sub(r'>\s*\d+\s+sources?\s*·\s*\d+\s+wiki pages?',
                     f'> {actual_raw} sources · {actual_wiki} wiki pages', idx)
        with open(index_path, 'w', encoding='utf-8') as f:
            f.write(idx)
    except Exception:
        pass


# ── Main entry point ─────────────────────────────────────────────

def create_wiki_page(vault_path, raw_path, url=None, llm_result=None,
                     hint_title=None, source=None, overwrite=False):
    """Create a wiki page from a raw source file.

    Args:
        vault_path: absolute path to the vault root
        raw_path: relative path to the raw file (e.g. "raw/webpages/slug.md")
        url: source URL (optional, extracted from content if missing)
        llm_result: dict with { title, summary, tags, related, body } from LLM (optional)
        hint_title: title hint from caller (optional)
        source: entry point name for logging
        overwrite: if True (refresh-wiki path), bypass both collision checks
                   AND snapshot the existing wiki page to .kb-trash/ before
                   writing the new one. Use this only for explicit re-process
                   flows — never for normal kb add ingest.

    Returns dict with { status, page_name, summary, wiki_path }
    """
    full_raw_path = os.path.join(vault_path, raw_path)

    # Read and pre-process raw content
    try:
        with open(full_raw_path, 'r', encoding='utf-8', errors='replace') as f:
            raw_text = f.read()
    except (IOError, OSError) as e:
        return {'status': 'failed', 'page_name': None, 'summary': f'Cannot read raw file: {e}'}

    parsed = preprocess_content(raw_text)
    body = parsed['body']
    if not url and parsed['url']:
        url = parsed['url']
    if not hint_title and parsed['title']:
        hint_title = parsed['title']

    # If pre-processing cleaned the content (stripped junk), update the raw file too
    # so Local Copy links show clean content, not Web Clipper DOM noise
    if len(body) < len(raw_text) * 0.9:
        try:
            yaml_match = re.match(r'^(---\n.*?\n---\n)', raw_text, re.DOTALL)
            header = yaml_match.group(1) if yaml_match else ''
            with open(full_raw_path, 'w', encoding='utf-8') as f:
                f.write(header + body + '\n')
        except Exception:
            pass

    # Normalize URL
    if url:
        url = re.sub(r'[?&](utm_\w+|s|t|rcm|ref|usp|si|igsh|fbclid)=[^&]*', '', url)
        url = re.sub(r'[?&]$', '', url).rstrip('/')

    # Determine source type from the raw_path by matching against configured
    # category directories. Substring match works for both flat
    # (`raw/papers/X.pdf`) and nested (`raw/papers/artifacts/X.pdf`) layouts.
    from config import raw_categories, wiki_format_dir
    source_type = 'webpage'
    wiki_subdir = wiki_format_dir('webpages')
    for cat_name, cat_cfg in raw_categories().items():
        if f'/{cat_name}/' in raw_path or raw_path.startswith(f'{cat_name}/'):
            source_type = cat_cfg['source_type']
            wiki_subdir = wiki_format_dir(cat_name)
            break

    # Use LLM result if available, otherwise build fallback data
    if llm_result and llm_result.get('title') and llm_result.get('body'):
        data = llm_result
        # Still apply naming convention to LLM title (enforce consistency)
        # LLM should already follow it, but this is the safety net
    else:
        data = build_fallback_data(body, url, source_type, hint_title)
        # Find related topics for fallback (LLM does this itself)
        data['related'] = find_related_topics(data['title'], vault_path)

    # Resolve source icon: favicon PNG > domain emoji > source_type
    # emoji > None. The wiki page renders fine at any tier.
    source_icon = None
    if _favicons is not None:
        if url:
            source_icon = _favicons.ensure_favicon(url, vault_path)
            if source_icon is None:
                source_icon = _favicons.domain_emoji(url)
                # If domain emoji is the generic globe, prefer the
                # source_type emoji — 📄/📺/🖼️ is more informative.
                if source_icon == '🌐':
                    source_icon = _favicons.source_type_emoji(source_type)
        else:
            source_icon = _favicons.source_type_emoji(source_type)

    # Detect a downloaded binary sibling (PDF, DOCX, XLSX, PPTX, EPUB)
    # next to raw_path. If present, the Local Copy link points to that
    # binary instead of the extracted .md — users clicking through
    # expect the rendered original, not the LLM-input intermediate.
    local_copy_path = _find_binary_sibling(vault_path, raw_path)

    # Rewrite raw-relative asset paths to wiki-relative paths.
    # The body string flows verbatim from raw → wiki, but the two files sit
    # at different depths in the vault tree. Without translation, every
    # backfilled asset reference would 404 in Obsidian's wiki preview.
    wiki_body = _rewrite_asset_paths_for_wiki(data.get('body', ''))

    # Build wiki page
    page_content = build_wiki_page(
        title=data['title'],
        summary=data.get('summary', ''),
        tags=data.get('tags', []),
        related=data.get('related', []),
        body=wiki_body,
        source_type=source_type,
        raw_path=raw_path,
        url=url,
        source_icon=source_icon,
        local_copy_path=local_copy_path,
    )

    # Sanity-check the assembled page through the canonical schema gate
    # before writing. This is the last common chokepoint for the build_*/
    # create_wiki_page path; routing through validate_wiki_frontmatter
    # means any future code that produces malformed frontmatter fails
    # loudly here instead of silently landing bad data.
    import sys as _sys
    _sys.path.insert(0, os.path.dirname(__file__))
    from wiki_schema import (  # type: ignore
        validate_wiki_frontmatter, _sanitize_wiki_title_for_filename, SchemaError,
    )
    try:
        # 0.11.3: cap matches `naming.filename_max_chars` (default 100) so
        # filenames don't get double-truncated. Previously a hard `[:75]`
        # here over-truncated past the word-boundary cut already done in
        # apply_naming_convention(truncate=True) — producing filenames
        # like "...for agents, tools, and MCP serv" (cut mid-word) and
        # "...the org-wide cata" (cut mid-word, the user-reported case).
        from config import naming as _naming
        _max = _naming().get('filename_max_chars', 130)
        sanitized = _sanitize_wiki_title_for_filename(data['title'])
        if len(sanitized) > _max:
            # Word-boundary cut: take the last space within the cap, then
            # strip trailing punctuation (commas, semicolons, etc.) so a
            # mid-clause cut doesn't leave a dangling `,` at end of filename.
            cut = sanitized[:_max]
            clean_title = cut.rsplit(' ', 1)[0] if ' ' in cut else cut
            clean_title = clean_title.rstrip(' ,;:—-.')
        else:
            clean_title = sanitized
        validate_wiki_frontmatter(page_content)
    except SchemaError as e:
        return {'status': 'schema_error', 'page_name': data.get('title', '?'),
                'summary': None, 'wiki_path': None, 'error': str(e)}

    wiki_dir = os.path.join(vault_path, wiki_subdir)
    os.makedirs(wiki_dir, exist_ok=True)
    wiki_path = os.path.join(wiki_dir, _safe_filename(clean_title) + '.md')

    # URL-identity collision check (the duplicate-page bug class).
    # The path-existence check below only catches same-filename collisions.
    # Same source URL with a different title on a second capture (e.g.
    # improved chrome strip extracts a better title) produces a different
    # filename — slips past os.path.exists and writes a duplicate.
    # url-resolved.tsv already maps URL -> page_name, so we look there
    # before writing. Verifies the wiki file actually exists on disk before
    # treating the TSV row as authoritative (user may have trashed it).
    # Skipped under overwrite=True: refresh-wiki explicitly intends to
    # rewrite the existing page, not avoid creating a duplicate.
    if not overwrite:
        existing_for_url = _find_wiki_page_for_url(url, vault_path)
        if existing_for_url:
            return {'status': 'exists', 'page_name': os.path.splitext(os.path.basename(existing_for_url))[0],
                    'summary': None,
                    'wiki_path': os.path.relpath(existing_for_url, vault_path)}

    # Collision check: if a file already occupies wiki_path, decide whether
    # it's a real duplicate or a stale stub stealing this slot from a
    # distinct source. The brijpandeyji incident produced a redirect stub
    # named "LinkedIn: Post LinkedIn" — the chrome-stripped LinkedIn title
    # collides for every future LinkedIn capture, so without this branch
    # every new LinkedIn post gets silently skipped as "exists".
    # Under overwrite=True we still snapshot the existing file to .kb-trash/
    # below before the atomic write — both checks are skipped, but recovery
    # is preserved.
    if not overwrite and os.path.exists(wiki_path):
        if _existing_page_matches_url(wiki_path, url):
            # Sanitize for the same reason as the created-path return
            # below — page_name must match the on-disk filename so the
            # wikilink resolves.
            return {'status': 'exists', 'page_name': _safe_filename(clean_title),
                    'summary': None,
                    'wiki_path': os.path.relpath(wiki_path, vault_path)}
        # Disambiguate: append a URL-derived tail so distinct sources
        # don't share the same wiki filename.
        clean_title = _disambiguate_title(clean_title, url, source_type)
        wiki_path = os.path.join(wiki_dir, _safe_filename(clean_title) + '.md')
        if os.path.exists(wiki_path):
            return {'status': 'exists', 'page_name': _safe_filename(clean_title),
                    'summary': None,
                    'wiki_path': os.path.relpath(wiki_path, vault_path)}

    # Snapshot the existing wiki page to .kb-trash/ before overwrite so
    # `kb undo` (or manual recovery) can restore the prior body if the
    # refresh produced something the user does not want. The atomic write
    # below would otherwise leave no recoverable copy.
    if overwrite and os.path.exists(wiki_path):
        try:
            import datetime as _dt_refresh
            import shutil as _shutil_refresh
            trash_base = os.path.join(vault_path, '.kb-trash')
            ts = _dt_refresh.datetime.now().strftime('%Y%m%d_%H%M%S')
            trash_dir = os.path.join(trash_base, f'{ts}_refresh_wiki')
            os.makedirs(trash_dir, exist_ok=True)
            _shutil_refresh.copy2(wiki_path, os.path.join(trash_dir, os.path.basename(wiki_path)))
        except Exception as _e:
            # Snapshot is a safety net; never block the write on it.
            print(f'[wiki_page] refresh snapshot failed (proceeding anyway): {_e}', file=sys.stderr)

    # Atomic write via tmp + rename — no partial pages on crash.
    tmp_path = wiki_path + '.tmp'
    with open(tmp_path, 'w', encoding='utf-8') as f:
        f.write(page_content)
    os.replace(tmp_path, wiki_path)

    # Invalidate bulk-llm-regen state for this path (0.10.24). The regen
    # script tracks completion by file path; when a wiki page is created
    # OR rewritten under the same path, the heuristic summary that
    # create_wiki_page produces overwrites whatever good summary the
    # regen script previously generated. Without invalidation, the next
    # regen pass would skip this path (state says "completed") and the
    # bait first-sentence summary would persist. Removing the entry
    # forces the next regen pass to re-generate an Opus summary.
    _invalidate_regen_state(vault_path, wiki_path)

    # Update tracking
    update_tracking(url, clean_title, vault_path)

    # Add bidirectional cross-references
    for rel_page in data.get('related', []):
        _add_backlink(vault_path, rel_page, clean_title)

    return {
        'status': 'created',
        # Return the sanitized form so wikilinks resolve to the actual
        # file on disk. On POSIX this is identical to clean_title; on
        # Windows the `:` gets substituted to ` —`, so reporting the
        # unsanitized clean_title would have produced a broken
        # [[Web: Foo]] link that doesn't open the on-disk Web — Foo.md.
        'page_name': _safe_filename(clean_title),
        'summary': data.get('summary'),
        'wiki_path': os.path.relpath(wiki_path, vault_path),
    }


_WIKI_SOURCE_DIRS = (
    'wiki/format/webpages',
    'wiki/format/repos',
    'wiki/format/papers',
    'wiki/format/videos',
    'wiki/format/images',
    'wiki/format/books',
)


def _find_wiki_page_for_url(url, vault_path):
    """Return absolute path of an existing wiki page whose URL canonicalizes
    to the same as `url`, or None.

    Uses inbox/url-resolved.tsv as the primary index (cheap O(rows) read
    instead of scanning every wiki page). Verifies the indexed page file
    actually exists on disk before claiming a hit — a TSV row whose wiki
    file was trashed or renamed should NOT block a fresh capture.

    Returns None on any error so the normal create flow proceeds.
    """
    if not url:
        return None
    tsv_path = os.path.join(vault_path, 'inbox', 'url-resolved.tsv')
    if not os.path.exists(tsv_path):
        return None
    try:
        import sys as _sys
        _sys.path.insert(0, os.path.dirname(__file__))
        from url_canonical import canonicalize  # type: ignore
        new_canon = canonicalize(url).url
    except Exception:
        return None
    try:
        with open(tsv_path, 'r', encoding='utf-8') as fh:
            for raw in fh:
                line = raw.rstrip('\n')
                if not line or line.startswith('#') or line.startswith('status\t'):
                    continue
                parts = line.split('\t')
                # update_tracking writes: status \t page_name \t url \t timestamp
                # Older rows have 5 cols (status, description, source_url, resolved_url, type).
                # In both shapes parts[1] is the page_name and parts[2] is the URL we care about.
                if len(parts) < 3:
                    continue
                page_name = parts[1].strip()
                row_url = parts[2].strip()
                if not page_name or not row_url:
                    continue
                try:
                    if canonicalize(row_url).url != new_canon:
                        continue
                except Exception:
                    continue
                # URL match — verify the wiki file still exists on disk.
                for sub in _WIKI_SOURCE_DIRS:
                    candidate = os.path.join(vault_path, sub, page_name + '.md')
                    if os.path.exists(candidate):
                        return candidate
                # TSV row pointed at a wiki file that no longer exists; keep
                # scanning in case a later row has a live page.
    except Exception:
        return None
    return None


def _existing_page_matches_url(wiki_path, new_url):
    """Return True iff the existing wiki page at wiki_path is a legitimate
    occupant of this slot for new_url.

    Returns False (caller must disambiguate) when:
      - the existing page is a redirect stub (`redirect: true`); a stub is
        a historical alias slot, not a real occupant
      - the existing page's source URL(s) canonicalize to something other
        than new_url

    Returns True (caller may safely return 'exists') when:
      - new_url is missing (no comparison possible — be conservative)
      - frontmatter is unparseable (be conservative)
      - existing source URL canonicalizes to the same thing as new_url
    """
    if not new_url:
        return True
    try:
        with open(wiki_path, 'r', encoding='utf-8') as fh:
            head = fh.read(8192)
    except (IOError, OSError):
        return True
    m = re.match(r'^---\n(.*?)\n---', head, re.DOTALL)
    if not m:
        return True
    fm = m.group(1)
    if re.search(r'^redirect:\s*("?true"?|true)\s*$', fm, re.MULTILINE):
        return False
    existing_urls = []
    m2 = re.search(r'^source:\s*"?([^"\n]+?)"?\s*$', fm, re.MULTILINE)
    if m2:
        existing_urls.append(m2.group(1).strip())
    m3 = re.search(r'^url:\s*"?([^"\n]+?)"?\s*$', fm, re.MULTILINE)
    if m3:
        existing_urls.append(m3.group(1).strip())
    m4 = re.search(r'^urls:\s*\n((?:[ \t]+-\s+.*\n?)+)', fm, re.MULTILINE)
    if m4:
        for line in m4.group(1).splitlines():
            mu = re.search(r'-\s+"?([^"\n]+?)"?\s*$', line)
            if mu:
                existing_urls.append(mu.group(1).strip())
    if not existing_urls:
        return True
    try:
        import sys as _sys
        _sys.path.insert(0, os.path.dirname(__file__))
        from url_canonical import canonicalize  # type: ignore
        new_canon = canonicalize(new_url).url
        for eu in existing_urls:
            if canonicalize(eu).url == new_canon:
                return True
        return False
    except Exception:
        return True


def _disambiguate_title(clean_title, url, source_type):
    """Append a URL-derived tail to clean_title to make a distinct filename.

    Used when an existing wiki page occupies the slot for a different URL
    (or is a redirect stub). The tail is the last few segments of the
    URL-derived slug — e.g. "sakawa-7457079005138108416" for a LinkedIn
    post — preserving enough of the URL to distinguish the source while
    keeping the title human-readable.

    Falls back to a short hash if URL slug derivation fails."""
    tail = ""
    try:
        import sys as _sys
        _sys.path.insert(0, os.path.dirname(__file__))
        from slug import derive_slug  # type: ignore
        cat_map = {'webpage': 'webpages', 'repo': 'repos', 'video': 'videos',
                   'paper': 'papers', 'image': 'images', 'book': 'books'}
        category = cat_map.get(source_type, 'webpages')
        url_slug = derive_slug(category, url, None)
        # Take the last 2-3 hyphen segments of the slug — typically the
        # author handle + status ID, the most-distinguishing portion.
        segments = url_slug.split('-')
        tail = '-'.join(segments[-3:])[:40]
    except Exception:
        if url:
            import hashlib
            tail = hashlib.sha1(url.encode('utf-8')).hexdigest()[:10]
    if not tail:
        return clean_title
    from wiki_schema import _sanitize_wiki_title_for_filename  # type: ignore
    new_title = f"{clean_title} ({tail})"
    return _sanitize_wiki_title_for_filename(new_title)[:100]


def _add_backlink(vault_path, target_page, source_page):
    """Add a backlink from target_page to source_page (both frontmatter and Connections)."""
    try:
        for wf in glob.glob(os.path.join(vault_path, 'wiki', '**', '*.md'), recursive=True):
            wf_name = os.path.splitext(os.path.basename(wf))[0]
            if wf_name != target_page:
                continue
            with open(wf, 'r', encoding='utf-8') as fh:
                content = fh.read()
            if source_page in content:
                break
            if 'related:\n' in content and f'[[{source_page}]]' not in content:
                content = content.replace('related:\n', f'related:\n  - "[[{source_page}]]"\n', 1)
            if '## Connections' in content and f'[[{source_page}]]' not in content:
                content = content.replace('## Connections\n', f'## Connections\n\n- [[{source_page}]]\n', 1)
            with open(wf, 'w', encoding='utf-8') as fh:
                fh.write(content)
            break
    except Exception:
        pass


# ── CLI entry point ──────────────────────────────────────────────

if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='Athena wiki page builder')
    subparsers = parser.add_subparsers(dest='command', help='Command')

    # Default: create wiki page (backwards-compatible with --vault --raw-path)
    parser.add_argument('--vault', help='Vault root path')
    parser.add_argument('--raw-path', help='Relative path to raw file')
    parser.add_argument('--url', help='Source URL')
    parser.add_argument('--title', help='Title hint')
    parser.add_argument('--source', default='shell', help='Entry point name')
    parser.add_argument('--llm-json', help='LLM result as JSON string')
    parser.add_argument('--stdin', action='store_true', help='Read JSON input from stdin')

    # journal subcommand
    jp = subparsers.add_parser('journal', help='Create journal entry')
    jp.add_argument('--vault', required=True, help='Vault root path')
    jp.add_argument('--text', help='Journal entry text')
    jp.add_argument('--recent', action='store_true', help='List recent entries')

    # insight subcommand
    ip = subparsers.add_parser('insight', help='Create insight page')
    ip.add_argument('--vault', required=True, help='Vault root path')
    ip.add_argument('--title', help='Insight title')
    ip.add_argument('--body', help='Insight body text')
    ip.add_argument('--list', action='store_true', help='List existing insights')

    args = parser.parse_args()

    if args.command == 'journal':
        if args.recent:
            entries = list_recent_journal(args.vault)
            print(json.dumps({'entries': entries}))
        else:
            result = create_journal_entry(args.vault, args.text or '')
            print(json.dumps(result))

    elif args.command == 'insight':
        if args.list:
            titles = list_insights(args.vault)
            print(json.dumps({'insights': titles}))
        else:
            result = create_insight_page(args.vault, args.title, args.body)
            print(json.dumps(result))

    elif args.stdin:
        data = json.loads(sys.stdin.read())
        result = create_wiki_page(
            vault_path=data['vault'],
            raw_path=data['raw_path'],
            url=data.get('url'),
            llm_result=data.get('llm_result'),
            hint_title=data.get('title'),
            source=data.get('source', 'stdin'),
        )
        print(json.dumps(result))

    elif args.vault and args.raw_path:
        llm_result = json.loads(args.llm_json) if args.llm_json else None
        result = create_wiki_page(
            vault_path=args.vault,
            raw_path=args.raw_path,
            url=args.url,
            llm_result=llm_result,
            hint_title=args.title,
            source=args.source,
        )
        print(json.dumps(result))

    else:
        parser.print_help()
