"""Lint auto-fix: a capture-deep document/carousel page must get a `## Slides`
section embedding its slides, automatically, on every lint pass.

A LinkedIn document post captured via capture-deep stores its slides in the raw
as `![View image](../../assets/<post>/<hash>.jpg)`; the wiki synthesis never
embeds them, so the deck is invisible on the page the user reads. The lint check
auto-embeds them in document order. Must be idempotent (run twice → no dup).

Run from vault root:
    python3 -m pytest tests/test_lint_embed_slides.py -v
"""
from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LIB = ROOT / "bin" / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

import cmd_lint  # type: ignore  # noqa: E402

POST = "li-doc-ugcpost-123"
SLIDES = ["aaaaaa.jpg", "bbbbbb.jpg", "cccccc.jpg"]


def _raw_with_slides(n: int) -> str:
    imgs = " ".join(
        f"![View image](../../assets/{POST}/{h})" for h in SLIDES[:n]
    )
    return (
        "---\n"
        'title: "Post | LinkedIn"\n'
        f'source: "https://www.linkedin.com/posts/x_doc-ugcPost-123-ab"\n'
        'clipped_via: "web-clipper-social"\n'
        "---\n\n# Doc\n\nbody text\n\n" + imgs + "\n"
    )


WIKI = (
    "---\n"
    'title: "LinkedIn Doc"\n'
    'source_type: "webpage"\n'
    f'raw_path: "raw/webpages/artifacts/{POST}.md"\n'
    'url: "https://www.linkedin.com/posts/x_doc-ugcPost-123-ab"\n'
    "tags: [webpage]\n"
    "---\n"
    "[Source](https://www.linkedin.com/posts/x_doc-ugcPost-123-ab)\n\n"
    "## Key Findings\n\n- a finding\n\n## Keywords\n[[webpage]]\n"
)


class EmbedSlidesLint(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kb-slides-lint-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        (self.tmp / "bin").symlink_to(ROOT / "bin")
        for d in ("wiki/format/webpages", "raw/webpages/artifacts", "inbox"):
            (self.tmp / d).mkdir(parents=True, exist_ok=True)
        (self.tmp / "raw/webpages/artifacts" / f"{POST}.md").write_text(
            _raw_with_slides(3), encoding="utf-8")
        self.wiki = self.tmp / "wiki/format/webpages/LinkedIn Doc.md"
        self.wiki.write_text(WIKI, encoding="utf-8")

    def _lint(self):
        cmd_lint.handle([], str(self.tmp))

    def test_embeds_slides_in_order_and_idempotent(self):
        self._lint()
        body = self.wiki.read_text(encoding="utf-8")
        self.assertIn("## Slides", body)
        # all three slides embedded, in document order
        pos = [body.index(f"![[raw/assets/{POST}/{h}]]") for h in SLIDES]
        self.assertEqual(pos, sorted(pos), "slides must be in document order")
        # placed before Keywords
        self.assertLess(body.index("## Slides"), body.index("## Keywords"))

        first = body
        self._lint()
        second = self.wiki.read_text(encoding="utf-8")
        # idempotent: exactly one Slides section, each embed once, no churn
        self.assertEqual(second.count("## Slides"), 1)
        for h in SLIDES:
            self.assertEqual(second.count(f"![[raw/assets/{POST}/{h}]]"), 1)
        self.assertEqual(first, second, "second lint must not change the page")

    def test_under_threshold_not_embedded(self):
        # Only 2 slides (< _SLIDE_MIN=3) → no Slides section.
        (self.tmp / "raw/webpages/artifacts" / f"{POST}.md").write_text(
            _raw_with_slides(2), encoding="utf-8")
        self._lint()
        self.assertNotIn("## Slides", self.wiki.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
