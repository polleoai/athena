"""arcus_html webpage-body cleaning: masthead titles + header chrome.

Anchor (2026-07-01): a cloud.google.com blog captured via arcus/kb-capture kept
the page's own header in the body — the title as an H1 twice (once with a
'| Google Cloud Blog' masthead + '&amp;' entity), a 'Developers & Practitioners'
breadcrumb, and four empty share-button bullets ('- '). Athena's raw template
already emits one '# {title}' H1, so all of that is redundant chrome.

Run from vault root:
    python3 -m pytest tests/test_clean_webpage_body.py -v
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LIB = ROOT / "bin" / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from arcus_html import _clean_webpage_body, _strip_masthead  # type: ignore  # noqa: E402


class StripMasthead(unittest.TestCase):
    def test_strips_site_masthead_suffix(self):
        self.assertEqual(
            _strip_masthead("Scaling LLM Inference: Multi-Node KV Cache Offloading | Google Cloud Blog"),
            "Scaling LLM Inference: Multi-Node KV Cache Offloading",
        )
        self.assertEqual(
            _strip_masthead("How we built the thing - Some Company Engineering"),
            "How we built the thing",
        )

    def test_keeps_short_or_legit_titles(self):
        # Too little would remain → leave untouched.
        self.assertEqual(_strip_masthead("Redis | Docs"), "Redis | Docs")
        # No masthead → unchanged.
        self.assertEqual(_strip_masthead("A perfectly normal article title here"),
                         "A perfectly normal article title here")


class CleanWebpageBody(unittest.TestCase):
    TITLE = "Scaling LLM Inference: Multi-Node KV Cache Offloading with GKE & Managed Lustre"

    def test_removes_duplicate_title_breadcrumb_and_empty_bullets(self):
        body = (
            "# Scaling LLM Inference: Multi-Node KV Cache Offloading with GKE &amp; Managed Lustre | Google Cloud Blog\n\n"
            "Developers & Practitioners\n\n"
            "# Scaling LLM Inference: Multi-Node KV Cache Offloading with GKE & Managed Lustre\n\n"
            "July 1, 2026\n\n"
            "-\n-\n-\n-\n\n"
            "The real article begins here and continues for a while.\n"
        )
        out = _clean_webpage_body(body, self.TITLE)
        self.assertFalse(out.startswith("#"))  # leading dup-title H1s gone
        self.assertNotIn("Developers & Practitioners", out)
        self.assertNotIn("Google Cloud Blog", out)
        self.assertNotIn("\n-\n", "\n" + out + "\n")  # empty bullets gone
        self.assertIn("The real article begins here", out)
        self.assertIn("July 1, 2026", out)  # legit publish date kept

    def test_keeps_real_leading_content(self):
        body = "This article opens directly with a real sentence of prose.\n\n## A Section\n\nMore.\n"
        self.assertEqual(_clean_webpage_body(body, self.TITLE), body.strip())

    def test_does_not_strip_a_nontitle_heading(self):
        body = "## Introduction\n\nContent under a heading that is not the title.\n"
        out = _clean_webpage_body(body, self.TITLE)
        self.assertIn("## Introduction", out)


if __name__ == "__main__":
    unittest.main()
