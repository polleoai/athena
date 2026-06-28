"""slide_deck — extract reveal.js HTML slide decks into athena markdown.

A reveal.js deck (the kind embedded via <iframe> on talk pages — see
embed_discovery) renders each slide from a <section> in the static HTML, but it
does NOT look like an article: readability-based extractors (arcus) return
empty. The slide text is fully present in the served HTML, so a section-walker
recovers it.

Scope: reveal.js specifically (class="reveal" + multiple <section>s). Other
slide hosts (Google Slides, SlideShare, slides.com) paint to canvas/JS and are
not statically extractable — those are out of scope here and fall through.

reveal.js nests vertical slides: <section><section>…</section></section>. Only
LEAF sections (no nested <section>) are real slides; the wrapping parent is a
stack. Nesting means a regex can't reliably split slides — this uses a
depth-aware HTMLParser.

Architecturally this is single-URL/single-format extraction, arcus's domain. It
lives in athena as a fallback (same precedent as the athena-side deep-mode path
in arcus_html.py) until arcus grows a slide provider.

  looks_like_slide_deck(html)        -> bool
  extract_slide_deck(html, url=...)  -> str | None   (markdown, or None if not a deck)
"""
from __future__ import annotations

import html as _html
import re
from html.parser import HTMLParser

_REVEAL_ROOT_RE = re.compile(r'class\s*=\s*["\'][^"\']*\breveal\b', re.IGNORECASE)
_SECTION_OPEN_RE = re.compile(r"<section\b", re.IGNORECASE)
_TITLE_RE = re.compile(r"<title\b[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_MIN_SLIDES = 3

_SKIP_TAGS = {"script", "style", "aside"}
_LINEBREAK_END = {"p", "div", "h1", "h2", "h3", "h4", "h5", "h6",
                  "ul", "ol", "pre", "blockquote", "tr", "figcaption", "section"}


class _DeckParser(HTMLParser):
    """Collect the text of leaf <section>s (one per real slide), in order."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.slides: list[str] = []
        self._section_depth = 0
        # Per open section: did it contain a nested <section>? (→ not a leaf)
        self._has_child: list[bool] = []
        self._buf: list[str] = []          # text buffer for the innermost section
        self._skip = 0                     # >0 while inside script/style/aside

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in _SKIP_TAGS:
            self._skip += 1
            return
        if tag == "section":
            if self._has_child:
                self._has_child[-1] = True   # parent now has a child → not leaf
            # Starting a new innermost section: flush the parent's buffer (it
            # belongs to a non-leaf and is discarded once a child opens).
            if self._section_depth >= 1:
                self._buf = []
            self._section_depth += 1
            self._has_child.append(False)
            self._buf = []
            return
        if self._skip or self._section_depth == 0:
            return
        if tag == "li":
            self._buf.append("\n- ")
        elif tag == "br":
            self._buf.append("\n")

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in _SKIP_TAGS:
            self._skip = max(0, self._skip - 1)
            return
        if tag == "section":
            if self._section_depth == 0:
                return
            is_leaf = not self._has_child[-1]
            if is_leaf:
                text = _clean("".join(self._buf))
                if text:
                    self.slides.append(text)
            self._has_child.pop()
            self._section_depth -= 1
            self._buf = []
            return
        if self._skip or self._section_depth == 0:
            return
        if tag in _LINEBREAK_END:
            self._buf.append("\n")

    def handle_data(self, data):
        if self._skip or self._section_depth == 0:
            return
        self._buf.append(data)


def _clean(text: str) -> str:
    """Normalise a slide's accumulated text: trim lines, collapse blank runs."""
    text = _html.unescape(text)
    lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in text.splitlines()]
    out: list[str] = []
    for ln in lines:
        if not ln and (not out or not out[-1]):
            continue
        out.append(ln)
    return "\n".join(out).strip()


def looks_like_slide_deck(html_text: str) -> bool:
    """True when `html_text` is a reveal.js deck worth section-walking."""
    if not html_text:
        return False
    if not _REVEAL_ROOT_RE.search(html_text):
        return False
    return len(_SECTION_OPEN_RE.findall(html_text)) >= _MIN_SLIDES


def extract_slide_deck(html_text: str, url: str = "") -> str | None:
    """Return a markdown rendering of a reveal.js deck, or None if not a deck.

    Output shape:
        # <deck title>

        > Slide deck · N slides · <url>

        ## Slide 1
        …text…
    """
    if not looks_like_slide_deck(html_text):
        return None
    parser = _DeckParser()
    try:
        parser.feed(html_text)
    except Exception:
        return None
    slides = [s for s in parser.slides if s]
    if len(slides) < _MIN_SLIDES:
        return None

    title = ""
    m = _TITLE_RE.search(html_text)
    if m:
        title = _html.unescape(re.sub(r"\s+", " ", m.group(1))).strip()

    parts: list[str] = []
    if title:
        parts.append(f"# {title}")
    meta = f"> Slide deck · {len(slides)} slides"
    if url:
        meta += f" · {url}"
    parts.append(meta)
    for i, slide in enumerate(slides, 1):
        parts.append(f"## Slide {i}\n\n{slide}")
    return "\n\n".join(parts).strip()
