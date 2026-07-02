"""process_clip._strip_share_widgets: drop 'share this page' widget links.

Anchor (2026-07-01): a cloud.google.com blog clipped via the Web Clipper's
browser-capture path landed the page's social-share bar (X intent / LinkedIn
shareArticle / Facebook sharer / mailto) as a 4-item bullet list under the
title. Those are page chrome, not content, and must be stripped at ingest.

Run from vault root:
    python3 -m pytest tests/test_strip_share_widgets.py -v
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LIB = ROOT / "bin" / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from process_clip import _strip_share_widgets  # type: ignore  # noqa: E402


class StripShareWidgets(unittest.TestCase):
    def test_removes_the_google_blog_share_bar(self):
        body = (
            "# Scaling LLM Inference\n\nJuly 1, 2026\n\n"
            "- https://x.com/intent/tweet?text=Scaling&url=https://cloud.google.com/blog/x\n"
            "- https://www.linkedin.com/shareArticle?mini=true&url=https://cloud.google.com/blog/x&title=Scaling\n"
            "- https://www.facebook.com/sharer/sharer.php?u=https://cloud.google.com/blog/x\n"
            "- mailto:?subject=Scaling&body=Check%20out\n\n"
            "##### Miro Nikolov\n\nReal article body here.\n"
        )
        out = _strip_share_widgets(body)
        for junk in ("intent/tweet", "shareArticle", "sharer.php", "mailto:?subject"):
            self.assertNotIn(junk, out, junk)
        self.assertIn("Real article body here.", out)
        self.assertIn("Miro Nikolov", out)

    def test_covers_other_share_hosts(self):
        body = (
            "- https://www.reddit.com/submit?url=https://ex.org/a\n"
            "- https://www.pinterest.com/pin/create/button/?url=https://ex.org/a\n"
            "- https://api.whatsapp.com/send?text=https://ex.org/a\n"
            "- https://t.me/share/url?url=https://ex.org/a\n"
            "- https://twitter.com/intent/tweet?url=https://ex.org/a\n"
            "Keep this line.\n"
        )
        out = _strip_share_widgets(body)
        self.assertEqual(out.strip(), "Keep this line.")

    def test_preserves_real_links_and_mid_sentence_mentions(self):
        body = (
            "Read the code at https://github.com/llm-d/llm-d for details.\n"
            "See https://x.com/intent/tweet mentioned inline stays put.\n"
            "- https://x.com/intent/tweet?url=https://ex.org/a\n"  # this bullet IS a widget → drop
        )
        out = _strip_share_widgets(body)
        self.assertIn("github.com/llm-d/llm-d", out)
        self.assertIn("mentioned inline stays put", out)
        # The standalone widget bullet is gone (only 1 intent URL — the inline one — remains).
        self.assertEqual(out.count("intent/tweet"), 1)

    def test_noop_on_clean_body(self):
        body = "# Title\n\nJust normal prose.\n\n## Section\n\nMore prose.\n"
        self.assertEqual(_strip_share_widgets(body), body)


if __name__ == "__main__":
    unittest.main()
