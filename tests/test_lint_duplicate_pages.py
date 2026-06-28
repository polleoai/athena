"""Lint: two wiki pages sharing one raw_path / url are flagged as duplicates.

Anchor (2026-06-27): the two ironcurtain.dev decks were each captured twice —
a hand-created `Slides:` page and an auto-synthesized `Web:` page pointing at the
SAME raw — so each appeared twice in the Recently Added table. This check flags
that structural duplicate.

Run from vault root:
    python3 -m pytest tests/test_lint_duplicate_pages.py -v
"""
from __future__ import annotations

import io
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LIB = ROOT / "bin" / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

import cmd_lint  # type: ignore  # noqa: E402


def _page(title: str, raw: str, url: str) -> str:
    return (
        "---\n"
        f'title: "{title}"\n'
        'source_type: "webpage"\n'
        f'raw_path: "{raw}"\n'
        f'url: "{url}"\n'
        "tags: [webpage]\n"
        "---\n"
        f"[Source]({url})\n\n## Key Findings\n\n- a finding\n\n## Keywords\n[[webpage]]\n"
    )


class DuplicatePageLint(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kb-dupe-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        (self.tmp / "bin").symlink_to(ROOT / "bin")
        for d in ("wiki/format/webpages", "raw/webpages/artifacts", "inbox"):
            (self.tmp / d).mkdir(parents=True, exist_ok=True)

    def _lint_output(self) -> str:
        buf = io.StringIO()
        with redirect_stdout(buf):
            cmd_lint.handle([], str(self.tmp))
        return buf.getvalue()

    def _write(self, name, raw, url):
        (self.tmp / "wiki/format/webpages" / f"{name}.md").write_text(
            _page(name, raw, url), encoding="utf-8"
        )

    def test_shared_raw_path_is_flagged(self):
        raw = "raw/webpages/artifacts/deck.md"
        self._write("Slides Deck", raw, "https://ex.org/a/")
        self._write("Web Deck", raw, "https://ex.org/b/")
        out = self._lint_output()
        self.assertIn("Duplicate raw_path (multiple wiki pages → same raw", out)
        self.assertIn("Slides Deck", out)
        self.assertIn("Web Deck", out)

    def test_distinct_pages_not_flagged(self):
        self._write("Page One", "raw/webpages/artifacts/one.md", "https://ex.org/1/")
        self._write("Page Two", "raw/webpages/artifacts/two.md", "https://ex.org/2/")
        out = self._lint_output()
        # The check still runs, but with zero duplicates it must not list any.
        self.assertNotIn("Duplicate wiki pages share one raw_path/url", out)


if __name__ == "__main__":
    unittest.main()
