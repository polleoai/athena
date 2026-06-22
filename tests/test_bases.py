"""Tests for bin/lib/cmd_bases.py — `kb bases` Obsidian Bases generator.

Run from vault root:  python3 -m pytest tests/test_bases.py -v
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

LIB = Path(__file__).resolve().parent.parent / "bin" / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

import cmd_bases  # noqa: E402

try:
    import yaml  # type: ignore
    _HAS_YAML = True
except Exception:  # pragma: no cover - venv may lack pyyaml
    _HAS_YAML = False


_PAPER = """\
---
title: "Test Paper"
source_type: "paper"
url: "https://arxiv.org/abs/1234.5678"
date_added: 2026-01-01
tags: [paper, test]
---
Body.
"""

_REPO = """\
---
title: "Test Repo"
source_type: "repo"
urls:
  - "https://github.com/foo/bar"
date_added: 2026-01-02
tags: [repo]
---
Body.
"""


def _build_vault(base: Path) -> str:
    """Create a tiny vault with papers + repos collections; images stays empty."""
    fmt = base / "wiki" / "format"
    for t in ("papers", "repos", "videos", "webpages", "images", "entities", "comparisons"):
        (fmt / t).mkdir(parents=True, exist_ok=True)
    (fmt / "papers" / "Test Paper.md").write_text(_PAPER, encoding="utf-8")
    # _Contents.md must NOT be counted as a page.
    (fmt / "papers" / "_Contents.md").write_text("scaffold", encoding="utf-8")
    (fmt / "repos" / "Test Repo.md").write_text(_REPO, encoding="utf-8")
    return str(base)


class BasesGeneration(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = _build_vault(Path(self._tmp.name))
        self.bases = Path(self.root) / "wiki" / "bases"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_per_collection_bases_exist_and_parse(self) -> None:
        rc = cmd_bases.handle([], self.root)
        self.assertEqual(rc, 0)
        papers = self.bases / "Papers.base"
        repos = self.bases / "Repos.base"
        self.assertTrue(papers.exists())
        self.assertTrue(repos.exists())
        if _HAS_YAML:
            pdoc = yaml.safe_load(papers.read_text(encoding="utf-8"))
            self.assertIn("filters", pdoc)
            self.assertIn("views", pdoc)
            self.assertEqual(pdoc["views"][0]["type"], "table")
            self.assertEqual(pdoc["views"][0]["name"], "Papers")
        else:  # structural substring fallback
            text = papers.read_text(encoding="utf-8")
            self.assertIn("type: table", text)
            self.assertIn("name: Papers", text)

    def test_folder_filter_per_type(self) -> None:
        cmd_bases.handle([], self.root)
        ptext = (self.bases / "Papers.base").read_text(encoding="utf-8")
        rtext = (self.bases / "Repos.base").read_text(encoding="utf-8")
        self.assertIn('file.inFolder("wiki/format/papers")', ptext)
        self.assertIn('file.inFolder("wiki/format/repos")', rtext)
        # Papers carries date_added (sort) + url; repos has no date_added sort.
        self.assertIn("- date_added", ptext)
        self.assertIn("- url", ptext)
        self.assertNotIn("date_added", rtext)

    def test_master_has_one_view_per_nonempty_collection(self) -> None:
        cmd_bases.handle([], self.root)
        master = self.bases / "All Knowledge.base"
        self.assertTrue(master.exists())
        if _HAS_YAML:
            doc = yaml.safe_load(master.read_text(encoding="utf-8"))
            # Two non-empty collections: papers + repos.
            self.assertEqual(len(doc["views"]), 2)
            names = {v["name"] for v in doc["views"]}
            self.assertEqual(names, {"Papers", "Repos"})
        else:
            text = master.read_text(encoding="utf-8")
            self.assertEqual(text.count("type: table"), 2)
            self.assertIn("name: Papers", text)
            self.assertIn("name: Repos", text)

    def test_empty_collection_produces_no_base(self) -> None:
        cmd_bases.handle([], self.root)
        self.assertFalse((self.bases / "Images.base").exists())
        self.assertFalse((self.bases / "Comparisons.base").exists())
        self.assertFalse((self.bases / "Videos.base").exists())

    def test_idempotent_byte_identical(self) -> None:
        cmd_bases.handle([], self.root)
        first = {p.name: p.read_bytes() for p in self.bases.glob("*.base")}
        cmd_bases.handle([], self.root)
        second = {p.name: p.read_bytes() for p in self.bases.glob("*.base")}
        self.assertEqual(first, second)

    def test_help_exits_zero_no_files(self) -> None:
        rc = cmd_bases.handle(["--help"], self.root)
        self.assertEqual(rc, 0)
        # --help must not generate the bases dir contents.
        self.assertFalse((self.bases / "Papers.base").exists())


if __name__ == "__main__":
    unittest.main()
