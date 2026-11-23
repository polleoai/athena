"""`kb backfill-fulltext`: restore the text arcus <= 0.8.1 cut at 50,000 chars.

arcus's PDF tiers silently cut text at 50,000 characters (arcus#18, fixed in
0.8.2), so paper raws hold only the first ~50k of papers that run 100-144k.
The user approved repairing them without a prompt per batch (2026-11-22,
"the pdf extraction should not have anything to do with me"), on terms:

  1. Grow-only: the missing tail is APPENDED to the raw's text section
     (before a closing "## Figures" section, if the figure backfill added
     one); nothing existing moves or changes, inserted figure lines included.
  2. The old text must be found verbatim in the new extraction (anchored on
     its last stretch, figure lines set aside); otherwise the raw is skipped
     and listed, never guessed at.
  3. A raw whose text is already whole is left alone; wiki pages are never
     touched; one snapshot batch per run (kb undo), audited; --dry-run.

Run from vault root:
    python3 -m pytest tests/test_backfill_fulltext.py -v
"""
from __future__ import annotations

import io
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
LIB = ROOT / "bin" / "lib"
TESTS = ROOT / "tests"
for p in (LIB, TESTS):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import test_pdf_fulltext_whole as W  # noqa: E402  (the ~108k-char fixture PDF)

FIG = "![Figure 1](../../assets/paper-a/figure-01-p1.png)"


@unittest.skipUnless(W.HAVE, "arcus with PyMuPDF not installed")
class Repair(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import arcus_file  # type: ignore
        cls.tmp = Path(tempfile.mkdtemp(prefix="athena-ft-fixture-"))
        cls.pdf = cls.tmp / "fixture.pdf"
        W.make_long_pdf(cls.pdf)
        cls.full = arcus_file._extract_via_arcus(cls.pdf, "pdf")["text"]

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        self.kb = Path(tempfile.mkdtemp(prefix="athena-ft-"))
        self.addCleanup(shutil.rmtree, self.kb, ignore_errors=True)
        (self.kb / "bin").symlink_to(ROOT / "bin")
        for d in ("wiki/format/papers", "raw/papers/artifacts", "inbox", ".athena"):
            (self.kb / d).mkdir(parents=True, exist_ok=True)
        art = self.kb / "raw/papers/artifacts"
        shutil.copyfile(self.pdf, art / "paper-a.pdf")
        cut = self.full[:50_000]  # what arcus <= 0.8.1 returned
        lines = cut.split("\n")
        lines.insert(3, FIG)  # the figure backfill inserted a line in the text
        self.raw = art / "paper-a.md"
        self.raw.write_text('---\ntitle: "A"\nsource: "https://arxiv.org/abs/0000.00000"\n---\n\n'
                            '- **PDF:** ./paper-a.pdf\n- **Content fetched:** yes\n- **Figures:** 1 kept\n\n'
                            '## Full Text\n\n' + "\n".join(lines) + "\n\n## Figures\n\n![x](../../assets/paper-a/figure-02-p2.png)\n")
        self.page = self.kb / "wiki/format/papers/A.md"
        self.page.write_text('---\ntitle: "A"\nraw_path: "raw/papers/artifacts/paper-a.md"\n---\n\nnotes\n')
        self.raw0, self.page0 = self.raw.read_bytes(), self.page.read_bytes()

    def run_cmd(self, *args):
        import cmd_backfill_fulltext  # type: ignore
        out = io.StringIO()
        with redirect_stdout(out), mock.patch("builtins.input", lambda *a: "n"):
            rc = cmd_backfill_fulltext.handle(list(args), str(self.kb))
        return rc or 0, out.getvalue()

    def test_tail_appended_grow_only_figures_kept(self):
        rc, out = self.run_cmd("--yes")
        self.assertEqual(rc, 0, out)
        new = self.raw.read_text()
        self.assertIn(W.END, new, "the cut-off end is back")
        old = self.raw0.decode()
        i = next(k for k in range(len(old)) if old[k] != new[k]) if old != new[:len(old)] else len(old)
        self.assertEqual(new[:i], old[:i])
        self.assertTrue(new.endswith(old[i:]), "pure insertion: all old text kept, in place")
        self.assertIn(FIG, new, "the inserted figure line is kept")
        self.assertLess(new.index(W.END), new.index("\n## Figures\n"), "text before the figures section")
        self.assertEqual(self.page.read_bytes(), self.page0, "wiki page untouched")
        self.assertIn("chars restored", out)

    def test_undo_reverts(self):
        self.run_cmd("--yes")
        import snapshot  # type: ignore
        ok, msg = snapshot.restore_last(str(self.kb))
        self.assertTrue(ok, msg)
        self.assertEqual(self.raw.read_bytes(), self.raw0)

    def test_dry_run_changes_nothing(self):
        rc, out = self.run_cmd("--dry-run")
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.raw.read_bytes(), self.raw0)
        self.assertIn("paper-a", out)

    def test_whole_raw_left_alone_and_rerun_is_a_no_op(self):
        self.run_cmd("--yes")
        after = self.raw.read_bytes()
        rc, out = self.run_cmd("--yes")
        self.assertEqual(self.raw.read_bytes(), after)
        self.assertIn("already whole", out)

    def test_old_text_not_in_new_is_skipped_and_listed(self):
        self.raw.write_text(self.raw.read_text().replace("Line 00500 of", "Line 00500 EDITED of"))
        before = self.raw.read_bytes()
        rc, out = self.run_cmd("--yes")
        self.assertEqual(self.raw.read_bytes(), before)
        self.assertIn("SKIPPED paper-a", out)


if __name__ == "__main__":
    unittest.main()
