"""Unit coverage for the paper-capture fixes in bin/kb-capture:

  * ``_title_from_raw``       — reads title: frontmatter from an existing raw.
  * ``_extract_pdf_fulltext`` — best-effort arcus PDF text; returns "" (never
                                raises) when the file is missing/unreadable.
  * ``handle_paper`` idempotency — when the raw already exists (e.g. a second
                                concurrent clip watcher raced us to it) the
                                handler returns the existing page as SUCCESS
                                instead of die()-ing, so the empty-body clip
                                recovery does not report a spurious failure.

Run from vault root:
    python3 -m pytest tests/test_kb_capture_paper_helpers.py -v
"""
from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from importlib.machinery import SourceFileLoader
from pathlib import Path
from unittest import mock

_VAULT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_VAULT / "bin" / "lib"))


def _load_kb_capture():
    path = _VAULT / "bin" / "lib" / "capture.py"
    loader = SourceFileLoader("kb_capture_mod", str(path))
    spec = importlib.util.spec_from_loader("kb_capture_mod", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


_KBC = _load_kb_capture()


class TestTitleFromRaw(unittest.TestCase):
    def _write(self, text: str) -> str:
        tmp = tempfile.NamedTemporaryFile(
            "w", suffix=".md", delete=False, encoding="utf-8")
        tmp.write(text)
        tmp.close()
        self.addCleanup(lambda: Path(tmp.name).unlink(missing_ok=True))
        return tmp.name

    def test_reads_quoted_title(self):
        p = self._write('---\ntitle: "Real Paper Title"\nsource: "x"\n---\n\nbody\n')
        self.assertEqual(_KBC._title_from_raw(p), "Real Paper Title")

    def test_reads_unquoted_title(self):
        p = self._write("---\ntitle: Bare Title\n---\n\nbody\n")
        self.assertEqual(_KBC._title_from_raw(p), "Bare Title")

    def test_missing_title_returns_empty(self):
        p = self._write("---\nsource: \"x\"\n---\n\nbody\n")
        self.assertEqual(_KBC._title_from_raw(p), "")

    def test_ignores_title_after_frontmatter(self):
        # A `title:`-looking line in the body must not be picked up.
        p = self._write("---\nsource: \"x\"\n---\n\ntitle: not this\n")
        self.assertEqual(_KBC._title_from_raw(p), "")

    def test_unreadable_file_returns_empty(self):
        self.assertEqual(_KBC._title_from_raw("/no/such/file.md"), "")


class TestExtractPdfFulltextGraceful(unittest.TestCase):
    def test_missing_pdf_returns_empty(self):
        # Best-effort enrichment: a bad path must yield "" (abstract-only body),
        # never an exception that would abort the whole capture.
        self.assertEqual(_KBC._extract_pdf_fulltext("/no/such/file.pdf"), "")

    def test_non_pdf_returns_empty(self):
        tmp = tempfile.NamedTemporaryFile(
            "w", suffix=".pdf", delete=False, encoding="utf-8")
        tmp.write("not a pdf")
        tmp.close()
        self.addCleanup(lambda: Path(tmp.name).unlink(missing_ok=True))
        self.assertEqual(_KBC._extract_pdf_fulltext(tmp.name), "")


class TestHandlePaperIdempotent(unittest.TestCase):
    def setUp(self):
        # die() would call _cleanup()+sys.exit(1); neutralize cleanup so a
        # regression (die instead of return) surfaces as SystemExit, not noise.
        p = mock.patch.object(_KBC, "_cleanup", lambda: None)
        p.start()
        self.addCleanup(p.stop)
        self.tmp = Path(tempfile.mkdtemp(prefix="kb-paper-idem-"))
        self.addCleanup(
            lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))

    def test_existing_raw_returns_success_not_die(self):
        papers = self.tmp / "raw/papers/artifacts"
        papers.mkdir(parents=True)
        raw = papers / "arxiv-2606-21071.md"
        raw.write_text(
            '---\ntitle: "Already Captured Paper"\n'
            'source: "https://arxiv.org/abs/2606.21071"\n---\n\nbody\n',
            encoding="utf-8")
        (papers / "arxiv-2606.21071.pdf").write_bytes(b"%PDF-1.4\n" + b"0" * 64)

        state: dict = {}
        # resolve_raw_path is what maps slug → the on-disk artifact path.
        with mock.patch.object(_KBC, "resolve_raw_path",
                               lambda rf, url: str(raw)):
            result = _KBC.handle_paper(
                "https://arxiv.org/abs/2606.21071",
                "arxiv-2606.21071", "2026-07-04",
                {"papers": str(papers)}, state)

        self.assertEqual(result, (str(raw), "Already Captured Paper"))
        # The sibling PDF is surfaced so the caller prints it as usual.
        self.assertEqual(state.get("pdf_file"),
                         str(papers / "arxiv-2606.21071.pdf"))


if __name__ == "__main__":
    unittest.main()
