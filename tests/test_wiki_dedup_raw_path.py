"""create_wiki_page dedups by raw_path, not just URL.

Anchor (2026-07-01): re-capturing a source (kb add on an existing URL) that the
LLM re-titles on re-synthesis slipped past the URL-identity check (which depends
on url-resolved.tsv being present + the URL canonicalizing identically) and wrote
a SECOND wiki page on the same raw — a duplicate. raw_path is the stable identity
of a source, so a raw_path check closes the gap.

Run from vault root:
    python3 -m pytest tests/test_wiki_dedup_raw_path.py -v
"""
from __future__ import annotations

import glob
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LIB = ROOT / "bin" / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

import wiki_page as wp  # type: ignore  # noqa: E402


class DedupByRawPath(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kb-wpdedup-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        (self.tmp / "CLAUDE.md").write_text("", encoding="utf-8")
        (self.tmp / "raw/webpages/artifacts").mkdir(parents=True)
        (self.tmp / "wiki/format/webpages").mkdir(parents=True)
        (self.tmp / "inbox").mkdir(parents=True)
        self.raw_rel = "raw/webpages/artifacts/example-com-x.md"
        (self.tmp / self.raw_rel).write_text(
            '---\ntitle: "X"\nsource: "https://example.com/x"\n---\n\n# X\n\nSome body.\n',
            encoding="utf-8",
        )

    def _create(self, title, url="https://example.com/x"):
        return wp.create_wiki_page(
            str(self.tmp), self.raw_rel, url=url,
            llm_result={"title": title, "summary": "s", "tags": ["webpage"],
                        "related": [], "body": "Real body text for the page."},
        )

    def _pages(self):
        return [p for p in glob.glob(str(self.tmp / "wiki/format/webpages" / "*.md"))
                if not os.path.basename(p).startswith("_")]

    def test_recapture_new_title_same_url_no_duplicate(self):
        r1 = self._create("First Title For The Page")
        self.assertEqual(r1["status"], "created")
        r2 = self._create("A Completely Different Retitled Page")
        self.assertEqual(r2["status"], "exists")
        self.assertEqual(r2["page_name"], r1["page_name"])
        self.assertEqual(len(self._pages()), 1, self._pages())

    def test_recapture_new_title_and_new_url_still_no_duplicate(self):
        # The raw_path check must catch it even when the URL index can't
        # (different URL form → url-resolved.tsv miss).
        r1 = self._create("First Title", url="https://example.com/x")
        self.assertEqual(r1["status"], "created")
        r2 = self._create("Retitled Again Differently", url="https://example.com/x?utm_source=y")
        self.assertEqual(r2["status"], "exists")
        self.assertEqual(len(self._pages()), 1, self._pages())

    def test_distinct_raw_still_creates_new_page(self):
        self._create("First Title", url="https://example.com/x")
        other_raw = "raw/webpages/artifacts/example-com-y.md"
        (self.tmp / other_raw).write_text(
            '---\ntitle: "Y"\nsource: "https://example.com/y"\n---\n\n# Y\n\nOther body.\n',
            encoding="utf-8",
        )
        r = wp.create_wiki_page(
            str(self.tmp), other_raw, url="https://example.com/y",
            llm_result={"title": "A Different Source Page", "summary": "s",
                        "tags": ["webpage"], "related": [], "body": "Body for Y."},
        )
        self.assertEqual(r["status"], "created")
        self.assertEqual(len(self._pages()), 2, self._pages())

    def test_helper_finds_page_by_raw_path(self):
        self._create("Some Title")
        found = wp._find_wiki_page_for_raw_path(self.raw_rel, str(self.tmp), "wiki/format/webpages")
        self.assertIsNotNone(found)
        self.assertIsNone(
            wp._find_wiki_page_for_raw_path("raw/webpages/artifacts/nope.md", str(self.tmp)))


if __name__ == "__main__":
    unittest.main()
