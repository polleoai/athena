"""Phase 0: `_best_title_from_url_slug` — the masthead-title fallback that
`unified_ingest` imports from `process_clip`. It was referenced but never
implemented, breaking the webpage ingest path (the `_best_title_from_url_slug`
ImportError behind 18 suite failures).

Contract (from unified_ingest's non-bait branch): when the Web Clipper lifted a
site/org masthead as the title while the real article headline sits in a body
H1 that matches the URL slug, return that H1; otherwise return None (keep the
clipped title). Conservative — must be a no-op for legitimate titles, so the
old↔new pipeline-equivalence tests stay green.

Run from vault root:
    python3 -m pytest tests/test_best_title_from_url_slug.py -v
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

LIB = Path(__file__).resolve().parent.parent / "bin" / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from process_clip import _best_title_from_url_slug  # type: ignore  # noqa: E402


class MastheadTitleFallback(unittest.TestCase):
    def test_masthead_replaced_by_slug_matching_h1(self):
        title = "Berkeley RDI"
        url = "https://rdi.berkeley.edu/blog/the-real-headline-about-agent-harnesses"
        body = ("Some intro paragraph.\n\n"
                "# The Real Headline About Agent Harnesses\n\n"
                "Body text follows.")
        self.assertEqual(
            _best_title_from_url_slug(title, body, url),
            "The Real Headline About Agent Harnesses",
        )

    def test_legit_title_matching_slug_preserved(self):
        # Title already matches the slug and there's no competing H1 → None.
        title = "Why Harnesses Eat Models"
        url = "https://example.substack.com/p/why-harnesses-eat-models"
        body = "Prose with no heading.\n\nMore prose about agents."
        self.assertIsNone(_best_title_from_url_slug(title, body, url))

    def test_no_h1_returns_none(self):
        self.assertIsNone(_best_title_from_url_slug(
            "Berkeley RDI",
            "Just prose, no headings at all.",
            "https://rdi.berkeley.edu/blog/the-real-headline-about-agent-harnesses",
        ))

    def test_short_slug_returns_none(self):
        # Slug too short / too few tokens to be a headline.
        self.assertIsNone(_best_title_from_url_slug(
            "Home", "# Welcome", "https://example.com/blog"))

    def test_h1_matching_existing_title_not_swapped(self):
        # When the title ALREADY matches the slug as well as the H1 does,
        # don't swap (no masthead situation).
        title = "The Real Headline About Agent Harnesses"
        url = "https://example.com/blog/the-real-headline-about-agent-harnesses"
        body = "# The Real Headline About Agent Harnesses\n\nBody."
        self.assertIsNone(_best_title_from_url_slug(title, body, url))


if __name__ == "__main__":
    unittest.main()
