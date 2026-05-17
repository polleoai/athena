"""Raw frontmatter parser — tolerates both YAML and legacy header-bullet form.

The Phase 1 schema-check baseline found that 233 of 360 raw files (65%)
use a legacy header-bullet metadata convention rather than YAML
frontmatter:

    # <Title here>

    - **URL:** https://example.com/...
    - **Authors:** Janko Gravner
    - **Captured:** 2026-04-06
    - **PDF:** ./local-file.pdf

This was the convention before the canonical-writer architecture
(write_raw_page) standardized on YAML. Old arxiv-converted PDFs and
hand-curated paper notes still use this form.

We tolerate it rather than force-migrate because:
  1. The body text below the header is what matters; the metadata
     can be reconstructed any time.
  2. Force-migrating 233 files would break wikilinks if title escaping
     differs.
  3. Phase 3 writers always emit YAML; over time the legacy share shrinks
     organically.

This parser returns a dict with the SAME keys as the YAML parser uses,
so RawFrontmatter (Pydantic) accepts both:

    {
      'title': '...',
      'source': 'https://...',  # mapped from 'URL:' bullet
      'captured_at': '...',     # mapped from 'Captured:' bullet
      'authors': '...',         # category-specific, tolerated by extra='allow'
    }

Pure function, no I/O beyond Path.read_text. Safe to import anywhere.
"""

from __future__ import annotations

import re
from pathlib import Path

from wiki_schema import read_frontmatter as _read_yaml_frontmatter


# Map legacy bullet labels (case-insensitive) to canonical schema keys.
# Adding a new label here teaches the parser to recognize one more
# legacy variant.
_LEGACY_BULLET_TO_KEY = {
    "url": "source",          # YAML uses 'source:', legacy used 'URL:'
    "source": "source",
    "captured": "captured_at",
    "captured_at": "captured_at",
    "clipped": "clipped_at",
    "clipped_at": "clipped_at",
    "authors": "authors",
    "author": "author",
    "stars": "stars",
    "language": "language",
    "topics": "topics",
    "last updated": "last_updated",
    "last_updated": "last_updated",
}

# Match a legacy header bullet line: `- **Label:** value`
_LEGACY_BULLET_RE = re.compile(r"^-\s+\*\*([^:*]+):\*\*\s*(.*)$")
_H1_RE = re.compile(r"^#\s+(.+)$")


def _parse_legacy_header(text: str) -> dict:
    """Parse the H1 + bullet-block convention. Returns {} if not legacy form."""
    lines = text.split("\n")
    # Find first non-blank line — must be H1
    i = 0
    while i < len(lines) and not lines[i].strip():
        i += 1
    if i >= len(lines):
        return {}
    h1 = _H1_RE.match(lines[i])
    if not h1:
        return {}

    fm: dict = {"title": h1.group(1).strip()}

    # Read the bullet block (skip blank lines, stop at first non-bullet)
    i += 1
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if not stripped:
            i += 1
            continue
        m = _LEGACY_BULLET_RE.match(line)
        if not m:
            break
        label = m.group(1).strip().lower()
        value = m.group(2).strip()
        # Strip leading "./..." (legacy PDF paths) — non-actionable here.
        key = _LEGACY_BULLET_TO_KEY.get(label)
        if key:
            fm[key] = value
        i += 1

    # If we found nothing beyond the title, this isn't really a legacy
    # header — could be a content-only markdown file. Return empty so
    # callers know to skip.
    if len(fm) <= 1:
        return {}
    return fm


def read_raw_frontmatter(path: Path) -> tuple[dict, str]:
    """Read raw frontmatter from `path`, tolerating both YAML and legacy.

    Returns (fm_dict, body). fm_dict is empty if neither format matches.
    The dict shape matches what RawFrontmatter (schemas.py) expects.

    YAML form takes precedence: if the file starts with `---\n...\n---`,
    we parse that and return. Otherwise we look for the H1 + bullet
    convention.
    """
    try:
        text = Path(path).read_text(encoding="utf-8")
    except (IOError, UnicodeDecodeError):
        return {}, ""

    # Try YAML first (the modern, canonical form)
    fm, body = _read_yaml_frontmatter(path)
    if fm:
        return fm, body

    # Fall back to legacy header-bullet
    legacy = _parse_legacy_header(text)
    if legacy:
        # Body is everything after the bullet block. Conservative split:
        # find the line after the last bullet and return the rest.
        lines = text.split("\n")
        i = 0
        while i < len(lines) and not lines[i].strip():
            i += 1
        # Skip H1
        if i < len(lines) and _H1_RE.match(lines[i]):
            i += 1
        # Skip bullet block
        while i < len(lines):
            stripped = lines[i].strip()
            if stripped and not _LEGACY_BULLET_RE.match(lines[i]):
                break
            i += 1
        body = "\n".join(lines[i:])
        return legacy, body

    return {}, text


# ─── Self-test ───────────────────────────────────────────────────────


if __name__ == "__main__":
    import tempfile

    LEGACY_SAMPLE = """# Probability Lecture Notes — UC Davis MAT135A

- **URL:** https://www.math.ucdavis.edu/~gravner/MAT135A/resources/lectures.pdf
- **Authors:** Janko Gravner
- **Captured:** 2026-04-06
- **PDF:** ./ucdavis-probability-lecture-notes.pdf

## Abstract

Lecture notes for MAT135A (Probability) at UC Davis...
"""

    YAML_SAMPLE = """---
title: "anthropics/skills"
source: "https://github.com/anthropics/skills"
captured_at: "2026-05-09T19:08:02Z"
---

# anthropics/skills

Repo content here...
"""

    NON_FRONTMATTER_SAMPLE = """Just some markdown text.

No headers, no bullets, just prose.
"""

    with tempfile.TemporaryDirectory() as td:
        for name, content, expected_keys, expect_body in [
            ("legacy.md", LEGACY_SAMPLE,
             {"title", "source", "authors", "captured_at"}, "## Abstract"),
            ("yaml.md", YAML_SAMPLE,
             {"title", "source", "captured_at"}, "# anthropics/skills"),
            ("nothing.md", NON_FRONTMATTER_SAMPLE,
             set(), "Just some markdown"),
        ]:
            p = Path(td) / name
            p.write_text(content)
            fm, body = read_raw_frontmatter(p)
            actual_keys = set(fm.keys())
            assert actual_keys == expected_keys, (
                f"{name}: expected keys {expected_keys}, got {actual_keys}"
            )
            assert expect_body in body, (
                f"{name}: body should contain {expect_body!r}, got {body[:100]!r}"
            )

    print("All raw_parser self-tests passed.")
