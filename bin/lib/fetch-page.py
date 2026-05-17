#!/usr/bin/env python3
"""Fetch page content via Playwright for JS-heavy or paywalled sites.

Usage: python3 fetch-page.py <url>

Navigates to the URL, waits for content to render, extracts the main
text content from the DOM. Used as a fallback when html2md/curl fail.

Exit codes:
  0 — success (text content on stdout)
  1 — failure (error message on stderr)
"""

import sys
import re


def _is_x_url(url):
    """Detect X.com / twitter.com tweet URLs."""
    return bool(re.match(r'https?://(www\.)?(x\.com|twitter\.com)/[^/]+/status/\d+', url))


def _strip_tweet_header_items(items):
    """Strip leading header-chrome items from a per-tweet item list.

    Each X tweet article DOM walks as something like:
        text("Harrison Chase\\n@hwchase17\\n")
        img(<avatar/banner>)
        text("Your harness, your memory\\n107\\n680\\n3.9K\\n1.9M\\nAgent harnesses...")

    Approach: find the first text item whose content has a substantive
    body line (> 80 chars OR ends with prose punctuation), strip header
    chrome from THAT item, and drop everything before it. The avatar
    image between header text items gets dropped too.
    """
    METRIC_RE = re.compile(r'^[\d.,]+(K|M|B)?$')
    HANDLE_RE = re.compile(r'^@[A-Za-z0-9_]+$')
    DATE_RE = re.compile(r'^[A-Z][a-z]+ \d+(, \d{4})?$')

    NAME_RE = re.compile(r"^[A-Z][\w'.-]*( [A-Z][\w'.-]*){0,3}$")  # "Harrison Chase"

    def _is_chrome_line(s):
        if not s: return True
        if HANDLE_RE.match(s) or METRIC_RE.match(s): return True
        if s in ('·', 'Article', 'Views', 'Show this thread'): return True
        if DATE_RE.match(s): return True
        return False

    # Build a flattened view of all lines across all text items.
    flat_lines = []  # (item_idx, line_idx, line_str)
    for idx, item in enumerate(items):
        if item['type'] != 'text': continue
        for li, ln in enumerate(item.get('value', '').split('\n')):
            flat_lines.append((idx, li, ln.strip()))

    # Two-phase scan:
    #  Phase 1: identify the title (line right before/after the metrics
    #           row) — this is the tweet headline like "Your harness,
    #           your memory" — keep it as a body lead.
    #  Phase 2: identify the body start (first long prose line after the
    #           metrics row).
    title_pos = None      # (item_idx, line_idx) of the title line
    metrics_seen = False
    body_pos = None       # (item_idx, line_idx) of first body prose
    for k, (idx, li, s) in enumerate(flat_lines):
        if not s: continue
        if HANDLE_RE.match(s) or _is_chrome_line(s) and METRIC_RE.match(s):
            metrics_seen = metrics_seen or METRIC_RE.match(s)
            continue
        if _is_chrome_line(s): continue
        if NAME_RE.match(s):
            follow_strs = [fl[2] for fl in flat_lines[k+1:k+8] if fl[2]]
            if any(HANDLE_RE.match(fs) for fs in follow_strs[:3]):
                continue
        # Short non-prose line followed by metrics → this is the title.
        # Keep it (we'll emit as the body's lead) but continue scanning.
        if not metrics_seen and len(s) < 80 and s[-1:] not in '.!?…":':
            follow_strs = [fl[2] for fl in flat_lines[k+1:k+8] if fl[2]]
            if any(METRIC_RE.match(fs) for fs in follow_strs[:4]):
                if title_pos is None:
                    title_pos = (idx, li, s)
                continue
        # First substantive body line.
        body_pos = (idx, li)
        break

    if body_pos is None:
        return items  # safe fallback

    body_text_idx, body_line_idx = body_pos
    out = []
    # If we found a title, emit it as the body lead (bolded so it
    # visually stands out as the tweet's headline).
    if title_pos:
        out.append({'type': 'text', 'value': f"**{title_pos[2]}**\n"})
    # Emit the body-start text item with its header lines stripped.
    body_item = items[body_text_idx]
    body_lines = body_item['value'].split('\n')
    stripped = '\n'.join(body_lines[body_line_idx:]).rstrip()
    if stripped:
        out.append({'type': 'text', 'value': stripped + '\n'})
    out.extend(items[body_text_idx + 1:])
    return out


def _strip_engagement_metrics(text):
    """Remove tweet engagement metric clusters from anywhere in body.

    Rows of short numeric / view-count lines aren't part of the tweet
    content; they're X's UI chrome (replies / reposts / likes / views,
    "Read N replies", "Show this thread"). Strip clusters of 2+
    consecutive metric lines wherever they appear:

      14
      99
      622
      136K        ← strip these 4

      1.9M
      Views
      107
      680
      3.9K
      10K
      Read 107 replies   ← strip these 7

    Mid-body clusters (e.g., between a parent tweet and an embedded
    quote-card's body) get cleaned too. Standalone single-number lines
    embedded in prose are NOT touched (they could be legitimate prose
    figures).
    """
    if not text:
        return text
    METRIC = re.compile(r'^[\d.,]+(K|M|B)?$')
    DATE = re.compile(r'^[A-Z][a-z]+ \d+(, \d{4})?$')
    # Tweet timestamp footer: "7:50 AM · Apr 11, 2026" — X chrome.
    TIMESTAMP_FOOTER = re.compile(r'^\d+:\d+\s*[AP]M\s*·\s*[A-Z][a-z]+\s*\d+,?\s*\d*$')
    # X page footer copyright: "© 2026 X Corp."
    COPYRIGHT_FOOTER = re.compile(r'^©\s*20\d\d\s*X Corp\.?$')
    KEYWORDS = {
        'Views', 'Show this thread',
        # X long-form / Premium upsell chrome.
        'Want to publish your own Article?',
        'Upgrade to Premium',
        'Subscribe',
        # X page footer items (when capture descends past tweet body
        # into the page chrome — common for innerText fallback).
        '|', 'Terms of Service', 'Privacy Policy', 'Cookie Policy',
        'Accessibility', 'Ads info', 'More', 'Blog', 'Careers',
        'Brand Resources', 'Advertising', 'Marketing',
        'X for Business', 'Developers', 'News', 'Settings',
        'About', 'Download the X app', 'Help Center',
    }
    READ_REPLIES = re.compile(r'^Read \d+ repl(y|ies)$')

    def _strip_quote_prefix(s):
        # Strip leading blockquote markers (`> ` or `>`) so metric
        # detection works on quoted X cards too. Returns (stripped,
        # quote_prefix_str). Re-applying the prefix preserves the quote
        # context for non-metric lines.
        m = re.match(r'^(>\s*)+', s)
        return (s[m.end():] if m else s, m.group(0) if m else '')

    def _is_metric_line(s):
        unquoted, _ = _strip_quote_prefix(s.strip())
        unquoted = unquoted.strip()
        if not unquoted: return False
        if METRIC.match(unquoted): return True
        if unquoted in KEYWORDS or unquoted == '·': return True
        if READ_REPLIES.match(unquoted): return True
        if DATE.match(unquoted): return True
        if TIMESTAMP_FOOTER.match(unquoted): return True
        if COPYRIGHT_FOOTER.match(unquoted): return True
        return False

    def _is_blank_or_quote_blank(s):
        # `>` (quote-blank) and `` (true blank) both count as separators
        # between metric clusters in blockquoted content.
        return s.strip() in ('', '>')

    lines = text.split('\n')
    out = []
    i = 0
    while i < len(lines):
        run_end = i
        run_count = 0
        j = i
        while j < len(lines):
            if _is_metric_line(lines[j]):
                run_count += 1
                run_end = j
                j += 1
            elif _is_blank_or_quote_blank(lines[j]) and run_count > 0 and j + 1 < len(lines) and _is_metric_line(lines[j + 1]):
                j += 1
            else:
                break
        if run_count >= 2:
            i = run_end + 1
            continue
        out.append(lines[i])
        i += 1
    return re.sub(r'\n{3,}', '\n\n', '\n'.join(out)).strip() + '\n'


def _strip_spa_chrome(text):
    """Strip SPA navigation chrome that html2md captures along with body
    content. Common across docs sites (Mintlify, Docusaurus, GitBook,
    Nextra, Vercel) — they all render a header/sidebar/search-bar shell
    that gets included in `inner_text`/`outerHTML` extraction.

    Strips, by line-anchored pattern:
      * "Skip to main content"
      * "Search..."
      * `⌘K` keyboard hint (and `Ctrl+K`)
      * "Ask AI" button label
      * "Navigation" sidebar header
      * "On this page" TOC header (before TOC bullets)
      * Bare hash-anchor invisible header markers like `## ​Title`
        (Mintlify uses zero-width-space prefix on auto-anchored headings)

    Doesn't truncate — strips line-by-line, preserving everything that
    isn't recognized chrome.
    """
    if not text:
        return text

    # Multi-line gist Star/Fork blocks. Several forms observed:
    #   Form A: - [Star\n1,088\n(1,088)](url)You must be signed in...
    #   Form B: - [Star\n1,088](url)You must be signed in...
    #   Form C: - [Star\n45    ← truncated (no closing bracket/paren/auth-suffix)
    # Form C happens when the html2md converter couldn't close the link.
    # Strip ALL of them; the count line and any orphan brackets get
    # caught too. Has to run BEFORE line-by-line processing because the
    # content spans line boundaries.
    text = re.sub(
        r'-\s*\[(Star|Fork)\s*\n[\d,]+\s*\n\([\d,]+\)\][^\n]*You must be signed[^\n]*\n?',
        '', text,
    )
    text = re.sub(
        r'-\s*\[(Star|Fork)\s*\n[\d,]+\][^\n]*You must be signed[^\n]*\n?',
        '', text,
    )
    # Form C: truncated `- [Star\n45` with no closing bracket. Match the
    # opener line + the next line containing only a number (with optional
    # K/M/B suffix). Conservatively limit to the case where the next
    # line is JUST a number — won't false-positive on real prose.
    text = re.sub(
        r'-\s*\[(Star|Fork)\s*\n[\d,]+(K|M|B)?\s*\n',
        '', text,
    )
    # GitHub gist "Save ... to your computer" and embed-script lines —
    # both contain the gist hash but the prefix/suffix differs by user
    # and gist. Strip by anchoring on the stable "Save"/"Clone this
    # repository" prefixes. Match with optional leading bullet/whitespace.
    text = re.sub(r'^[-*\s]*Save [\w./]+ to your computer and use it in GitHub Desktop\.\s*$\n?',
                  '', text, flags=re.MULTILINE)
    text = re.sub(r'Clone this repository at <script src="[^"]+"></script>\s*\n?', '', text)
    # "Learn more about clone URLs" markdown link — appears as a
    # standalone line in gist captures, sometimes twice (modal + main).
    text = re.sub(r'^\[Learn more about clone URLs\]\([^)]+\)\s*$\n?',
                  '', text, flags=re.MULTILINE)

    CHROME_LINES = {
        'Skip to main content',
        'Search...',
        'Search',
        '⌘K',
        '⌘KAsk AI',
        'Ask AI',
        'Navigation',
        'On this page',
        'On this page-',
        'Table of contents',
        'Edit this page',
        'Was this page helpful?',
        'Yes',
        'No',
        'Suggest edits',
        'Last updated',
        # Interactive tutorial / app widgets
        'Start',
        'Mark Complete',
        'Mark as Complete',
        'Mark as Read',
        'Continue',
        'Next', 'Previous',
        '×',  # modal close button
        # GitHub gist / repo page chrome
        'Show Gist options',
        'Embed',
        'Select an option',
        'Embed this gist in your website.',
        'Copy sharable link for this gist.',
        'Clone using the web URL.',
        'Learn more about clone URLs',
        'No results found',
        'Share',
        'Clone via HTTPS',
        'Instantly share code, notes, and snippets.',
        'Code',
        'Revisions',
        'Stars',
        'Forks',
    }
    # Also drop tutorial-progress strings like "0/10 completed",
    # "3/10 completed", etc.
    PROGRESS_RE = re.compile(r'^\d+/\d+ (completed|done|read)$')
    # Empty `### ` headings (from interactive modal scaffolds)
    EMPTY_HEADING_RE = re.compile(r'^#+\s*$')
    # GitHub gist Star/Fork count chrome lines like:
    #   - [Star
    #   1,088
    #   (1,088)](https://gist.github.com/login?...)You must be signed in...
    # These collapse to a multi-line link with parenthesized count and
    # a "You must be signed in" suffix. Detect by the suffix string
    # since it's stable across all gists.
    GIST_AUTH_SUFFIX = re.compile(r'You must be signed in to (star|fork) (a|this) gist')
    SPA_NAV_LINK_LINES_DROP_AFTER_HEADER = False  # off by default
    out = []
    for line in text.split('\n'):
        s = line.strip()
        # Also detect chrome lines with leading markdown markers
        # (`- Embed`, `# Select an option`, `* Share`).
        s_unmarked = re.sub(r'^[-*+]\s+', '', s)
        s_unmarked = re.sub(r'^#+\s+', '', s_unmarked)
        if s in CHROME_LINES or s_unmarked in CHROME_LINES: continue
        if PROGRESS_RE.match(s): continue
        if EMPTY_HEADING_RE.match(s): continue
        # GitHub gist Star/Fork action chrome — drop entire line
        # containing the auth-redirect suffix.
        if GIST_AUTH_SUFFIX.search(line): continue
        # Mintlify's zero-width-space-prefixed anchored headings — strip
        # the ZWSP so the heading reads cleanly.
        if '## ​' in line: line = line.replace('​', '')
        if '### ​' in line: line = line.replace('​', '')
        if '#### ​' in line: line = line.replace('​', '')
        out.append(line)
    text = '\n'.join(out)
    # Reduce excessive leading indentation on lines that aren't intentional
    # markdown indentation (real list nesting / code blocks). Cap at 4
    # leading spaces — Markdown treats 4+ as code block, which we don't
    # want for prose lines that just happened to be indented in HTML.
    out2 = []
    for line in text.split('\n'):
        if line and line[0] == ' ' and line.lstrip().startswith('-'):
            # Bullet item: collapse to single-level indentation.
            out2.append('- ' + line.lstrip()[2:].lstrip() if line.lstrip().startswith('- ') else line.lstrip())
        elif line.startswith('    ') and not line.startswith('     '):
            # 4-space-indented prose line that's not a deeper code block.
            out2.append(line.lstrip())
        elif line.startswith(' ' * 8):
            # Very-deep indentation (8+ spaces) on prose — flatten.
            out2.append(line.lstrip())
        else:
            out2.append(line)
    text = '\n'.join(out2)
    return re.sub(r'\n{3,}', '\n\n', text)


def _truncate_at_x_chrome(text):
    """Cut tweet body at the first X.com page-chrome marker.

    Tweet captures sometimes descend past the actual tweet body into X's
    page chrome — `Trending in ...`, `What's happening`, `New to X?`,
    `Read N replies`, the page footer, etc. Cluster-based metric strip
    handles dense chrome blocks but misses single-line markers that
    indicate "everything past here is page chrome." This function finds
    the EARLIEST such marker and truncates.

    Patterns matched (regex, multi-line):
      * `Trending in [A-Z][a-z]+` (any country/region)
      * `^What.s happening$` and similar trending headers
      * `^Politics · Trending$` and other category markers
      * `Read \d+ repl(y|ies)`
      * `^New to X\?` (signup prompt)
      * `^Don.t miss what.s happening`
      * Page footer: `^© 20\d\d X Corp` (catches the boundary even when
        cluster strip didn't)
    """
    if not text:
        return text
    # Optional leading bullet marker `- ` to catch chrome lines that
    # earlier passes incorrectly bulleted.
    BL = r"^(-\s+)?"
    patterns = [
        BL + r"Trending in [A-Z][\w ]+$",
        BL + r"Trending now$",
        BL + r"What['’]s happening$",
        BL + r"Who to follow$",
        BL + r"Relevant people$",
        BL + r"Politics · Trending$",
        BL + r"Sports · Trending$",
        BL + r"Entertainment · Trending$",
        BL + r"Read \d+ repl(y|ies)$",
        BL + r"New to X\?$",
        BL + r"Don['’]t miss what['’]s happening",
        BL + r"© 20\d\d X Corp",
        BL + r"Sign up with Apple",
        BL + r"Sign up with Google",
        BL + r"Sign up now to get your own",
        BL + r"Create account$",
        BL + r"By signing up, you agree to the Terms",
        r"^Show more$\nTerms",
    ]
    cuts = []
    for p in patterns:
        m = re.search(p, text, re.MULTILINE)
        if m:
            cuts.append(m.start())
    if cuts:
        return text[:min(cuts)].rstrip() + '\n'
    return text


def _bullet_lists_after_colon(text):
    """Detect prose lists that follow a colon-ending lead-in.

    Pattern:
        Thank you to a few people for review and thoughts:
        Sydney Runkle, who is doing a lot of great Deep Agents and memory work
        Viv Trivedy, who is a leading voice on agent harnesses
        ...

    The colon-ending line strongly signals "list follows." Each
    subsequent non-blank line that's medium-length (15-300 chars) and
    doesn't have terminal sentence-ending punctuation followed by a
    capital starter (i.e., is a single coherent item) gets bulleted.
    Stop when we hit a blank line or a line that looks like prose
    paragraph (long with terminal punctuation followed by capitalized
    next line).
    """
    if not text:
        return text
    lines = text.split('\n')
    out = []
    i = 0
    while i < len(lines):
        cur = lines[i]
        out.append(cur)
        cur_stripped = cur.rstrip()
        # Lead-in: line ending with `:` (excluding markdown header `:` cases)
        if cur_stripped.endswith(':') and len(cur_stripped) > 10 and len(cur_stripped) < 200:
            j = i + 1
            list_items = []
            while j < len(lines):
                nxt = lines[j]
                nxt_stripped = nxt.strip()
                if not nxt_stripped: break
                # Don't bullet lines already starting with markdown markers
                if nxt_stripped.startswith(('- ', '* ', '> ', '#', '|')): break
                # Item: 15-300 chars, not ending in mid-sentence punctuation
                # that suggests prose continuation. Allow ending with no
                # punct, or with `.`, `,`, etc.
                if 8 <= len(nxt_stripped) <= 300:
                    list_items.append(j)
                    j += 1
                    continue
                break
            # Only convert to bullets if we found 2+ list items.
            if len(list_items) >= 2:
                for idx in list_items:
                    if not lines[idx].lstrip().startswith('- '):
                        lines[idx] = '- ' + lines[idx].strip()
                # Replay these lines into out (they were not yet appended)
                for idx in list_items:
                    out.append(lines[idx])
                i = list_items[-1] + 1
                continue
        i += 1
    return '\n'.join(out)


def _merge_inline_div_breaks(text):
    """X wraps inline content (links, individual words) in
    `<div dir="ltr">` tags. Treating those as block elements during DOM
    walk produces spurious line breaks like:
        code
        . That code is the harness
        Sarah Wooders wrote a great blog
         on why "memory isn't a plugin"
    Rejoin lines that are clearly the same prose paragraph: a line that
    looks like inline-link aftermath followed by a punctuation- or
    short-content continuation.
    """
    if not text:
        return text
    lines = text.split('\n')
    out = []
    i = 0
    while i < len(lines):
        cur = lines[i]
        while i + 1 < len(lines):
            nxt = lines[i + 1]
            cur_stripped = cur.rstrip()
            nxt_stripped = nxt.lstrip()
            if not cur_stripped or not nxt_stripped:
                break
            # Real paragraph break: cur ends with sentence punctuation +
            # next starts with a capital letter (and nxt is reasonably long).
            if (cur_stripped[-1] in '.!?…' and nxt_stripped[:1].isupper()
                    and len(nxt_stripped) > 10):
                break
            # Merge if next line starts with punctuation continuation.
            if nxt_stripped[:1] in ',;:)]':
                cur = cur_stripped + nxt_stripped
                i += 1
                continue
            # Merge if cur ends with an opening bracket.
            if cur_stripped[-1:] == '(':
                cur = cur_stripped + nxt_stripped
                i += 1
                continue
            # Merge if next line is very short (≤ 3 chars) and consists
            # of just punctuation/connector — orphaned period/comma
            # that broke off after a linked word ("there was 512k lines
            # of code\n. That code...").
            if len(nxt_stripped) <= 3 and re.match(r'^[.,;:!?\s]+\S?', nxt_stripped):
                cur = cur_stripped + nxt_stripped
                i += 1
                continue
            # Merge if cur is short and looks like a linked phrase
            # ending in a noun/verb (no terminal punctuation) AND
            # next line starts with lowercase or quote (continuing prose).
            if (cur_stripped[-1].isalnum() and
                    (nxt_stripped[:1].islower() or nxt_stripped[:1] in '"\'(' or
                     nxt_stripped.startswith('on '))):
                cur = cur_stripped + ' ' + nxt_stripped
                i += 1
                continue
            break
        out.append(cur)
        i += 1
    return '\n'.join(out)


def _resolve_tco_links(text):
    """Resolve t.co short URLs to their final destinations. X displays
    long URLs through its t.co shortener; the short form is opaque
    (`https://t.co/abc123`) — useless for archival. Resolve them at
    capture time so the local copy preserves the real link.
    """
    import urllib.request, urllib.error
    seen = set()
    for short in re.findall(r'https?://t\.co/[A-Za-z0-9]+', text):
        if short in seen: continue
        seen.add(short)
        try:
            req = urllib.request.Request(short, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=8) as r:
                final = r.url
            if final and final != short:
                text = text.replace(short, final)
        except (urllib.error.URLError, urllib.error.HTTPError, Exception):
            pass  # leave unresolved on failure
    return text


def fetch_x_tweet(url):
    """Specialized X.com tweet fetcher returning structured output:
       {'text': str, 'images': [url, ...]}

    Walks each `<article role="article">` (one per tweet in the thread)
    and extracts BOTH its tweet text and its attached images together,
    so the returned text positions images inline near the tweet they
    belong to instead of dumping every image at the end. Same-content
    images at different formats/sizes are deduped by the stable media
    ID (e.g., `HF_xyz` portion of `pbs.twimg.com/media/HF_xyz.jpg`),
    so a screenshot served as both .jpg and .png — which used to render
    twice locally — collapses to one reference.
    """
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 1500})
        page.set_extra_http_headers({"User-Agent": "Mozilla/5.0 (Macintosh) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36"})
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
        except Exception:
            browser.close()
            return {'text': '', 'images': []}
        try: page.wait_for_selector('[data-testid="tweetText"]', timeout=15000)
        except Exception: page.wait_for_timeout(3000)
        for _ in range(8):
            page.evaluate("window.scrollBy(0, 1200)")
            page.wait_for_timeout(600)
        page.wait_for_timeout(1000)

        # Per-article extraction with DOM-order walk. Each <article
        # role="article"> represents one tweet. Within an article, walk
        # text and image nodes in document order so the resulting
        # markdown places images at their actual rendered positions
        # (inline within the prose), not all at the end. The per-article
        # boundary preserves the thread/reply structure; the in-order
        # walk preserves the inline image positioning.
        per_tweet = page.evaluate("""
            () => {
                function mediaId(url) {
                    const m = url.match(/pbs\\.twimg\\.com\\/media\\/([A-Za-z0-9_-]+)/);
                    return m ? m[1] : null;
                }
                function isHeaderChrome(text, hasMetrics) {
                    // Heuristic: lines that are part of the tweet header
                    // (author name, handle, timestamp, "Article" badge,
                    // metrics row of just numbers).
                    const t = text.trim();
                    if (!t) return true;
                    if (t.match(/^@[A-Za-z0-9_]+$/)) return true;
                    if (t.match(/^[\\d.,]+(K|M|B)?$/)) return true;
                    if (t === '·' || t === 'Article') return true;
                    if (t.match(/^[A-Z][a-z]+ \\d+$/)) return true;  // "Apr 3"
                    if (t.match(/^\\d+:\\d+ [AP]M · /)) return true;  // "7:50 AM ·"
                    if (t === 'Views') return true;
                    if (t === 'Show this thread' || t.match(/^Read \\d+ repl/)) return true;
                    return false;
                }
                function walkInOrder(art) {
                    var BLOCK_TAGS = new Set([
                        'DIV','P','SECTION','LI','UL','OL',
                        'BLOCKQUOTE','H1','H2','H3','H4','H5','H6','HEADER','FOOTER'
                    ]);
                    const out = [];
                    const seen = new Set();
                    const state = {buffer: ''};
                    function flushBuffer() {
                        if (state.buffer.trim()) {
                            out.push({type:'text', value: state.buffer});
                        }
                        state.buffer = '';
                    }
                    function recurse(node) {
                        if (node.nodeType === Node.TEXT_NODE) {
                            state.buffer += node.textContent;
                            return;
                        }
                        if (node.nodeType !== Node.ELEMENT_NODE) return;
                        if (node.tagName === 'IMG') {
                            if (node.currentSrc &&
                                node.currentSrc.includes('pbs.twimg.com/media') &&
                                node.naturalWidth > 100) {
                                const id = mediaId(node.currentSrc);
                                if (id && seen.has(id)) return;
                                if (id) seen.add(id);
                                flushBuffer();
                                out.push({
                                    type:'img',
                                    url: node.currentSrc.replace(/&name=\\w+/, '&name=large'),
                                });
                            }
                            return;
                        }
                        if (node.tagName === 'BR') {
                            state.buffer += '\\n';
                            return;
                        }
                        // Skip nested article/quote-card containers — they
                        // are handled as separate top-level entries.
                        if (node !== art && node.tagName === 'ARTICLE') return;
                        // List items: X renders bullets via CSS, not in
                        // text content. Detect <li> (or div/span with
                        // computed style list-item) and prepend `- ` so
                        // the markdown reconstructs the bullets visually.
                        var isLI = node.tagName === 'LI';
                        if (!isLI && (node.tagName === 'DIV' || node.tagName === 'SPAN')) {
                            try {
                                var cs = window.getComputedStyle(node);
                                if (cs && cs.display === 'list-item') isLI = true;
                            } catch (e) {}
                        }
                        if (isLI) {
                            // Ensure we're at line start before emitting bullet.
                            if (state.buffer && !state.buffer.endsWith('\\n')) state.buffer += '\\n';
                            state.buffer += '- ';
                        }
                        const isBlock = BLOCK_TAGS.has(node.tagName);
                        for (const child of node.childNodes) recurse(child);
                        if (isBlock && state.buffer && !state.buffer.endsWith('\\n')) {
                            state.buffer += '\\n';
                        }
                    }
                    recurse(art);
                    flushBuffer();
                    // Strip the leading text section if it's pure header
                    // chrome (author/handle/timestamp/metrics row).
                    if (out.length && out[0].type === 'text') {
                        const lines = out[0].value.split('\\n');
                        let startIdx = 0;
                        let sawHandle = false, sawMetrics = false;
                        for (let i = 0; i < Math.min(lines.length, 14); i++) {
                            const ln = lines[i].trim();
                            if (!ln) { startIdx = i + 1; continue; }
                            if (isHeaderChrome(ln, sawMetrics)) {
                                if (ln.match(/^[\\d.,]+(K|M|B)?$/)) sawMetrics = true;
                                if (ln.match(/^@/)) sawHandle = true;
                                startIdx = i + 1; continue;
                            }
                            // First non-header line — done.
                            startIdx = i;
                            break;
                        }
                        if (startIdx > 0) {
                            const stripped = lines.slice(startIdx).join('\\n').trim();
                            if (stripped) out[0] = {type:'text', value: stripped + '\\n'};
                            else out.shift();
                        }
                    }
                    return out;
                }
                const articles = Array.from(document.querySelectorAll('article[role="article"]'));
                const results = [];
                for (const art of articles) {
                    const items = walkInOrder(art);
                    if (items.length) results.push({items});
                }
                return results;
            }
        """)

        # Fallback: if articles didn't yield anything, fall back to the
        # tweetText-flat selector (legacy behavior).
        legacy_tweets = []
        legacy_images = []
        if not per_tweet:
            legacy_tweets = page.evaluate("""
                () => {
                    const seen = new Set(); const out = [];
                    for (const t of document.querySelectorAll('[data-testid="tweetText"]')) {
                        const txt = (t.innerText || '').trim();
                        if (txt.length > 10 && !seen.has(txt)) { seen.add(txt); out.push(txt); }
                    }
                    return out;
                }
            """)
            legacy_images = page.evaluate("""
                () => Array.from(document.querySelectorAll('img'))
                    .filter(i => i.currentSrc && i.currentSrc.includes('pbs.twimg.com/media'))
                    .filter(i => i.naturalWidth > 100)
                    .map(i => i.currentSrc.replace(/&name=\\w+/, '&name=large'))
            """)
        body_fallback = page.inner_text("body") if (not per_tweet and not legacy_tweets) else ""
        browser.close()

    # Emit text+images per tweet, deduped by media ID across the whole
    # thread. Each tweet is an ordered list of {type: 'text'|'img', ...}
    # items walked from the article DOM in document order — so images
    # appear at their actual rendered positions inline with prose, not
    # clumped at the end of each tweet section.
    seen_media = set()
    sections = []
    all_image_urls = []

    def _render_section(items_list):
        stripped_items = _strip_tweet_header_items(items_list)
        parts = []
        for item in stripped_items:
            if item['type'] == 'text':
                val = item.get('value', '')
                if not val.strip(): continue
                val = _merge_inline_div_breaks(val)
                val = _reconstruct_broken_urls(val)
                val = _resolve_tco_links(val)
                val = _restore_paragraph_breaks(val)
                val = _truncate_at_x_chrome(val)
                val = _strip_engagement_metrics(val)
                val = _bullet_lists_after_colon(val)
                parts.append(val.rstrip())
            elif item['type'] == 'img':
                url = item['url']
                m = re.search(r'pbs\.twimg\.com/media/([A-Za-z0-9_-]+)', url)
                key = m.group(1) if m else url
                if key in seen_media: return None  # signal: dedup hit, skip section
                seen_media.add(key)
                all_image_urls.append(url)
                parts.append(f"![]({url})")
        return "\n\n".join(p for p in parts if p)

    if per_tweet:
        rendered = []  # list of (handle_or_none, section_text)
        for entry in per_tweet:
            items_list = entry.get('items', [])
            # Detect the @handle for this entry — used for inlining
            # subsequent entries at the position of their handle mention
            # in earlier entries.
            handle = None
            for it in items_list:
                if it['type'] != 'text': continue
                m = re.search(r'@([A-Za-z0-9_]+)', it.get('value', ''))
                if m: handle = m.group(1); break
            section = _render_section(items_list)
            if section: rendered.append((handle, section))

        # Inline embedded quote/card sections (entries 2+) into the
        # parent section at the position where their handle is
        # referenced. If no reference is found, fall back to appending
        # with a `---` divider.
        if rendered:
            primary_handle, primary = rendered[0]
            for sub_handle, sub_section in rendered[1:]:
                if sub_handle and f'@{sub_handle}' in primary:
                    # Inline the sub-section right after the line that
                    # mentions @handle. Use a blockquote style so the
                    # embedded card is visually distinct.
                    quoted = '\n'.join('> ' + ln if ln else '>' for ln in sub_section.split('\n'))
                    pattern = re.compile(r'(^.*@' + re.escape(sub_handle) + r'.*$)', re.MULTILINE)
                    primary, n = pattern.subn(r'\1\n\n' + quoted.replace('\\', r'\\'), primary, count=1)
                    if n == 0:
                        primary = primary + '\n\n' + quoted
                else:
                    primary = primary + '\n\n---\n\n' + sub_section
            sections.append(primary)
        text = "\n\n---\n\n".join(sections)
    elif legacy_tweets:
        text = "\n\n---\n\n".join(legacy_tweets)
        for u in legacy_images:
            m = re.search(r'pbs\.twimg\.com/media/([A-Za-z0-9_-]+)', u)
            key = m.group(1) if m else u
            if key in seen_media: continue
            seen_media.add(key)
            all_image_urls.append(u)
        text = _reconstruct_broken_urls(text)
        text = _resolve_tco_links(text)
        text = _restore_paragraph_breaks(text)
        if all_image_urls:
            text = text.rstrip() + "\n\n" + "\n\n".join(f"![]({u})" for u in all_image_urls) + "\n"
    elif body_fallback:
        m = re.search(r'\bConversation\b', body_fallback)
        text = body_fallback[m.end():].strip() if m else ""
        # Strip X.com page chrome that follows the actual tweet content.
        # Use regex search so each marker can match patterns ("Read 11
        # replies") without us pre-enumerating numbers. Pick the EARLIEST
        # match across all markers — earliest = most aggressive trim.
        chrome_patterns = (
            r"Don[’']t miss what[’']s happening",
            r"Log in\nSign up",
            r"About\nDownload the X app",
            r"Terms of Service",
            r"© 20\d\d X Corp",
            r"Privacy Policy\s*\n\s*\|",
            r"Cookie Policy",
            r"Show more\nTerms",
            r"\bNew to X\?",
            r"Sign up now to get your own",
            r"Read \d+ repl",
            r"Show this thread",
            r"^\s*Sign up with Apple",
        )
        cuts = [m.start() for p in chrome_patterns
                if (m := re.search(p, text, re.MULTILINE))]
        if cuts:
            text = text[:min(cuts)].strip()
        if len(text) < 50: text = ""
    else:
        text = ""

    # Body-fallback path: text was extracted from page.inner_text("body").
    # No per-article structure available; apply post-processing then
    # append any images we got from the legacy selector path.
    if text and not per_tweet and not legacy_tweets:
        text = _reconstruct_broken_urls(text)
        text = _resolve_tco_links(text)
        text = _restore_paragraph_breaks(text)
        if all_image_urls:
            text = text.rstrip() + "\n\n" + "\n\n".join(f"![]({u})" for u in all_image_urls) + "\n"

    # `images` return field gives the deduped flat list of all media URLs
    # found across the thread (caller-facing convenience; the composed
    # `text` already has them positioned correctly).
    return {'text': text, 'images': all_image_urls[:8]}


def _fetch_x_thread(page, url):
    """X.com tweets are JS-rendered conversations. Walk the rendered DOM,
    pull the tweet text from every `[data-testid="tweetText"]` element,
    return joined. Selecting by tweetText directly (rather than requiring
    an `<article>` wrapper) is more resilient — X has changed wrapper
    markup multiple times but the tweetText testid is stable.

    Fixes the silent-truncation case where Athena previously got only the
    oembed root tweet (or nothing) and missed every reply/follow-up in a
    thread."""
    page.goto(url, wait_until="domcontentloaded", timeout=45000)
    # Try the canonical selector. If it doesn't appear, X may have shown
    # a login wall — fall through to body-text extraction below.
    selector_found = False
    try:
        page.wait_for_selector('[data-testid="tweetText"]', timeout=15000)
        selector_found = True
    except Exception:
        page.wait_for_timeout(3000)
    # X lazy-loads thread tweets on scroll; trigger a few scrolls.
    for _ in range(10):
        page.evaluate("window.scrollBy(0, 1200)")
        page.wait_for_timeout(600)
    page.wait_for_timeout(1500)
    tweets = page.evaluate("""
        () => {
            const seen = new Set();
            const out = [];
            for (const t of document.querySelectorAll('[data-testid="tweetText"]')) {
                const txt = (t.innerText || '').trim();
                if (txt.length > 10 && !seen.has(txt)) {
                    seen.add(txt);
                    out.push(txt);
                }
            }
            return out;
        }
    """)
    if not tweets:
        # Login-wall case: tweetText didn't render, but the page often
        # still has the canonical tweet content visible in body text
        # between "Conversation" and the footer/login UI. Slice it out.
        body = page.inner_text("body") or ""
        if not body:
            return None
        # Locate the Conversation marker and the trailing footer markers.
        m_start = re.search(r'\bConversation\b', body)
        if not m_start:
            return None
        sliced = body[m_start.end():]
        # Strip from where the "Don't miss" / "Log in" / footer cluster begins.
        for marker in ("Don’t miss what’s happening",
                       "Don't miss what's happening",
                       "Log in\nSign up",
                       "About\nDownload the X app"):
            i = sliced.find(marker)
            if i != -1:
                sliced = sliced[:i]
                break
        # Strip from "More replies" or login footer if those are first.
        sliced = sliced.strip()
        if len(sliced) < 50:
            return None
        # Restore paragraph breaks — X collapses them in body innerText.
        return _restore_paragraph_breaks(sliced)
    # Reconstruct URLs that X visually breaks across lines.
    joined = "\n\n---\n\n".join(tweets)
    joined = _reconstruct_broken_urls(joined)
    return joined


def _restore_paragraph_breaks(text):
    """X tweets often render as flat inline content; `inner_text` returns
    one long block (or single-newline-separated lines). Recovery
    heuristics:

      * Inline markers (mid-paragraph): insert paragraph break before
        numbered items (`NN —`), arrow bullets (`→ `), emoji section
        headers (`🔧 Setup`), or `Update March DD,` timestamps.
      * Single-newline-between-long-lines: convert to double newline.
        This handles the X long-form article case where each paragraph
        ends up on its own line but markdown collapses them when rendered.

    Idempotent: applying twice doesn't add extra blank lines because
    each pattern checks "not already at paragraph break."
    """
    if not text:
        return text
    NOT_AT_LINE_START = r'(?<!^)(?<!\n)'
    text = re.sub(NOT_AT_LINE_START + r'(\s)(\d{1,3}\s*[—\-–]\s+\S)', r'\n\n\2', text)
    text = re.sub(NOT_AT_LINE_START + r'(\s)(→ )', r'\n\n\2', text)
    text = re.sub(NOT_AT_LINE_START + r'(\s)([\U0001F300-\U0001FAFF]\s+[A-Z][A-Za-z &/]+)', r'\n\n\2', text)
    text = re.sub(NOT_AT_LINE_START + r'(\s)(Update\s+[A-Z][a-z]+\s+\d+[, ])', r'\n\n\2', text)

    # Prose paragraph recovery: single newline between two reasonably-long
    # lines becomes a paragraph break. Markdown renders single newlines
    # as soft line breaks (or just spaces); this gives the markdown
    # renderer the explicit double newline that matches X's visual
    # paragraph spacing.
    lines = text.split('\n')
    out_lines = []
    for i, line in enumerate(lines):
        out_lines.append(line)
        if i + 1 >= len(lines): break
        next_line = lines[i + 1]
        if (line.strip() and next_line.strip() and
                len(line.strip()) > 30 and len(next_line.strip()) > 30 and
                # Don't double-up if the next line is already a list item
                not re.match(r'^\s*[-*•]\s', next_line) and
                # Don't double-up after lines ending with comma/colon (likely flowing prose)
                line.rstrip().endswith(('.', '!', '?', '"', "'", ')', ']', '…'))):
            out_lines.append('')
    text = '\n'.join(out_lines)

    # Collapse 3+ blank lines to 2.
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip() + '\n'


def _reconstruct_broken_urls(text):
    """X.com displays long URLs broken across visual lines, like:
        http://\\ngithub.com/NicholasSpisak\\n/second-brain\\n…
    Collapse those back into one URL.

    Strategy: anchor on `https://` or `http://` followed by a newline,
    then greedily consume up to 6 short URL-character lines (until we
    hit a blank line, an ellipsis sentinel, or a non-URL character).
    """
    pattern = re.compile(
        r'(https?://)\n((?:[a-zA-Z0-9._/\-?=&%#~+]+\n){1,6})',
    )
    def collapse(m):
        scheme = m.group(1)
        lines = m.group(2).splitlines()
        # Stop at the first line that's pure ellipsis or empty.
        clean = []
        for ln in lines:
            ln_stripped = ln.strip().replace('…', '')
            if not ln_stripped:
                break
            clean.append(ln_stripped)
        return scheme + ''.join(clean)
    text = pattern.sub(collapse, text)
    # Drop trailing ellipsis right after a URL.
    text = re.sub(r'(https?://\S+)…', r'\1', text)
    return text


def _render_via_html2md(page, url):
    """Render the page in Playwright, get the post-JS HTML, pipe it
    through html2md for structure-preserving markdown extraction.

    For JS-rendered SPAs (Kaggle, Notion, GitBook, etc.), the static
    HTML returned by curl/html2md-with-url is essentially empty —
    content is hydrated client-side. Rendering first via Playwright,
    then converting the live DOM's outerHTML, gives structure-aware
    markdown (headings, lists, images, links) instead of the flat-text
    fallback that loses every semantic boundary.
    """
    import os, subprocess
    here = os.path.dirname(os.path.abspath(__file__))
    html2md = os.path.join(here, 'html2md.mjs')
    if not os.path.isfile(html2md):
        return None
    try:
        # Three-stage wait. domcontentloaded fires when the HTML is parsed
        # (fast path for static pages). networkidle catches SPAs whose
        # article body is hydrated after DCL — Microsoft Learn / Docusaurus /
        # Mintlify-style sites previously returned only the server-rendered
        # banner because we sampled the page before hydration. Bounded at 8s
        # because many sites never reach true idle (websockets, analytics
        # polling, lazy-loaded ads). Final 3.5s sleep covers the JS-late
        # hydration case where the network is idle but the renderer hasn't
        # painted yet.
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        try:
            page.wait_for_load_state("networkidle", timeout=8000)
        except Exception:
            pass
        page.wait_for_timeout(3500)
        html = page.evaluate("() => document.documentElement.outerHTML")
    except Exception:
        return None
    try:
        # Pass --url so html2md can resolve relative hrefs/img-srcs
        # against the actual page URL. Without this, "./assets/foo.png"
        # stays relative in markdown — and the asset pipeline can't
        # download a relative URL.
        result = subprocess.run(
            ['node', html2md, '--url', url], input=html,
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0 and len(result.stdout.strip()) > 200:
            md = _resolve_relative_urls_in_markdown(result.stdout, url)
            return _strip_spa_chrome(md)
    except (subprocess.TimeoutExpired, OSError):
        pass
    return None


def _resolve_relative_urls_in_markdown(text, base_url):
    """Resolve any remaining relative image/link URLs in markdown to
    absolute URLs based on the page's source URL.

    Defense-in-depth: html2md gets `--url` so it should resolve at
    extraction time, but some relative paths slip through (e.g., when
    the page uses `<base href>` overrides, or html2md's resolver misses
    a corner case). This pass catches any `![alt](./foo)` or
    `![alt](../foo)` or `![alt](relative.png)` references and rewrites
    them to absolute URLs that the asset pipeline can download.

    Same treatment for `[label](./relative)` text links.
    """
    if not text or not base_url:
        return text
    import urllib.parse

    def _abs(rel):
        try:
            return urllib.parse.urljoin(base_url, rel)
        except Exception:
            return rel

    def _is_relative(u):
        if not u: return False
        if u.startswith(('http://', 'https://', 'data:', 'mailto:', 'javascript:', 'tel:', '#')):
            return False
        # Already-local asset references (`../../assets/<slug>/<sha>.ext`)
        # — these are paths into Athena's content-addressed asset store,
        # NOT relative URLs to be resolved against the page's source URL.
        # Misinterpreting them broke 5 pages by rewriting valid local
        # references to nonexistent absolute URLs.
        if '../../assets/' in u or u.startswith(('assets/', '/assets/')):
            return False
        return True

    # Image references
    text = re.sub(
        r'(!\[[^\]]*\]\()([^)\s]+)(\s+"[^"]*")?(\))',
        lambda m: m.group(1) + (_abs(m.group(2)) if _is_relative(m.group(2)) else m.group(2))
                  + (m.group(3) or '') + m.group(4),
        text,
    )
    # Inline link references — but only when the URL is clearly a path
    # (contains `/` or starts with `.`); skip plain words inside [...]()
    # like `[Letta_AI](Letta_AI)` style cross-references.
    def _link_sub(m):
        url = m.group(2)
        if _is_relative(url) and ('/' in url or url.startswith('.')):
            return m.group(1) + _abs(url) + m.group(3)
        return m.group(0)
    text = re.sub(r'(\[[^\]]+\]\()([^)\s]+)(\))', _link_sub, text)
    return text


def fetch_page(url):
    """Fetch page content using Playwright. Routes X.com tweet URLs through
    a thread-aware extractor that walks every <article> on the conversation
    page; falls back to the generic main-content extractor for other URLs."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_extra_http_headers({
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/131.0.0.0 Safari/537.36"
        })

        # X.com tweets need thread-aware extraction.
        if _is_x_url(url):
            try:
                tweets_text = _fetch_x_thread(page, url)
                if tweets_text and len(tweets_text.strip()) > 50:
                    browser.close()
                    return clean_text(tweets_text)
            except Exception:
                pass  # fall through to generic extractor

        # Generic SPA-friendly path: render via Playwright, then run
        # the rendered HTML through html2md for structure preservation
        # (headings, bullets, links, images). Falls back to plain-text
        # extraction only if this returns nothing.
        rendered_md = _render_via_html2md(page, url)
        if rendered_md and len(rendered_md.strip()) > 200:
            browser.close()
            return rendered_md

        page.goto(url, wait_until="domcontentloaded", timeout=20000)

        # Dismiss LinkedIn login modals if present
        try:
            dismiss = page.query_selector('[aria-label="Dismiss"], .modal__dismiss, [data-tracking-control-name="public_post_feed-join-form-dismiss"]')
            if dismiss:
                dismiss.click()
                page.wait_for_timeout(500)
        except Exception:
            pass

        # Wait for main content to render
        try:
            page.wait_for_selector("article, main, [role='main'], .article-body, .feed-shared-update-v2",
                                   timeout=10000)
        except Exception:
            page.wait_for_timeout(5000)

        # Try extracting from article/main containers first
        # LinkedIn-specific selectors added
        for selector in ["article", ".feed-shared-update-v2__description", ".article-content", "main", "[role='main']", ".article-body"]:
            el = page.query_selector(selector)
            if el:
                text = el.inner_text()
                if text and len(text.strip()) > 100:
                    browser.close()
                    return clean_text(text)

        # Fallback: extract all body text
        text = page.inner_text("body")
        browser.close()

        if text and len(text.strip()) > 100:
            return clean_text(text)

        return None


def clean_text(text):
    """Clean up extracted page text."""
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = re.sub(r'\t+', ' ', text)
    # Remove common UI artifacts
    text = re.sub(r'(Sign up|Log in|Sign in|Cookie|Accept all).*\n?', '', text,
                  flags=re.IGNORECASE)
    return text.strip()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 fetch-page.py <url> [--x-tweet]", file=sys.stderr)
        sys.exit(1)

    url = sys.argv[1]
    is_x_mode = '--x-tweet' in sys.argv

    try:
        if is_x_mode or _is_x_url(url):
            # Structured output: text on stdout, images on stderr (one URL
            # per line, prefixed with `IMG: `). Lets bash callers parse
            # both without needing JSON.
            result = fetch_x_tweet(url)
            if result['text']:
                print(result['text'])
                for img in result['images']:
                    print(f"IMG: {img}", file=sys.stderr)
                sys.exit(0)
            else:
                print("No content found (X)", file=sys.stderr)
                sys.exit(1)
        result = fetch_page(url)
        if result:
            print(result)
            sys.exit(0)
        else:
            print("No content found", file=sys.stderr)
            sys.exit(1)
    except ImportError:
        print("Playwright not installed", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
