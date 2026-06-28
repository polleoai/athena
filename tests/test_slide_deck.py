"""slide_deck — reveal.js HTML deck → athena markdown.

Anchor (2026-06-27): the two ironcurtain.dev decks embedded on provos.org are
reveal.js. arcus returns empty for them (not articles); this extractor recovers
the slide text from the static <section>s.

Run from vault root:
    python3 -m pytest tests/test_slide_deck.py -v
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LIB = ROOT / "bin" / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

import slide_deck as sd  # type: ignore  # noqa: E402

REVEAL = """
<html><head><title>My Talk | Conf</title></head><body>
<div class="reveal"><div class="slides">
  <section><h2>Intro</h2><p>Detection is not enough.</p></section>
  <section>
    <section><h2>Problem</h2><ul><li>500 bugs</li><li>40k instances</li></ul></section>
    <section><h2>Detail</h2><p>intent drift over turns</p></section>
  </section>
  <section><h2>Fix</h2><p>security invariants</p></section>
</div></div>
</body></html>
"""


class LooksLikeDeck(unittest.TestCase):
    def test_reveal_with_enough_sections(self):
        self.assertTrue(sd.looks_like_slide_deck(REVEAL))

    def test_plain_article_is_not_a_deck(self):
        html = "<html><body><article><section>a</section></article></body></html>"
        self.assertFalse(sd.looks_like_slide_deck(html))

    def test_reveal_but_too_few_sections(self):
        html = '<div class="reveal"><section>only one</section></div>'
        self.assertFalse(sd.looks_like_slide_deck(html))

    def test_empty(self):
        self.assertFalse(sd.looks_like_slide_deck(""))


class ExtractDeck(unittest.TestCase):
    def test_extracts_title_and_leaf_slides(self):
        md = sd.extract_slide_deck(REVEAL, url="https://ex.org/talk/")
        self.assertIsNotNone(md)
        self.assertTrue(md.startswith("# My Talk | Conf"))
        self.assertIn("> Slide deck · 4 slides · https://ex.org/talk/", md)
        # 4 LEAF slides (the wrapping vertical-stack <section> is not counted)
        self.assertEqual(md.count("## Slide "), 4)
        self.assertIn("Detection is not enough.", md)
        self.assertIn("- 500 bugs", md)
        self.assertIn("security invariants", md)

    def test_non_deck_returns_none(self):
        self.assertIsNone(
            sd.extract_slide_deck("<article><p>just prose</p></article>")
        )

    def test_slides_in_document_order(self):
        md = sd.extract_slide_deck(REVEAL)
        self.assertLess(md.index("Detection is not enough"), md.index("500 bugs"))
        self.assertLess(md.index("500 bugs"), md.index("security invariants"))


if __name__ == "__main__":
    unittest.main()
