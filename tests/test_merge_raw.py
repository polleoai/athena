"""`kb merge-raw <twin> --into "<page>"` and figures once per merged paper.

5 arXiv papers had a page-less twin raw (same source as the raw behind the
wiki page). kb merge merges PAGES, so the user approved a raw-level merge
(2026-11-22) that does to the twin what kb merge does to a loser's raw:

  1. The twin joins the page's raw_paths (raw_path becomes raw_paths), and
     is marked merged_into: "<page>" (kb merge's format). No body changes;
     both raws keep their full text.
  2. One snapshot batch (kb undo restores page and twin), an audit entry,
     Proceed? unless --yes.
  3. Refused: a twin with a different source, a raw already on the page, a
     missing page or raw.
  4. kb backfill-figures treats a page's raw group as one paper: the figures
     go once, onto the first member (raw_paths order) with a local PDF.
  5. A new capture of a source whose every raw is merged still lands on an
     existing raw, not a new twin (in the AARM group both raws are marked).

Run from vault root:
    python3 -m pytest tests/test_merge_raw.py -v
"""
from __future__ import annotations

import io
import json
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

ABS = "https://arxiv.org/abs/2605.00055"
PAGE = "arXiv: Ambient Persuasion"


def _raw(src, body="text " * 100, extra=""):
    return f'---\ntitle: "P"\nsource: "{src}"\n{extra}---\n\n- **Content fetched:** yes\n\n## Full Text\n\n{body}\n'


class _Vault(unittest.TestCase):
    def setUp(self):
        self.kb = Path(tempfile.mkdtemp(prefix="athena-mr-"))
        self.addCleanup(shutil.rmtree, self.kb, ignore_errors=True)
        (self.kb / "bin").symlink_to(ROOT / "bin")
        for d in ("wiki/format/papers", "raw/papers/artifacts", "inbox", ".athena"):
            (self.kb / d).mkdir(parents=True, exist_ok=True)
        self.art = self.kb / "raw/papers/artifacts"
        self.primary = self.art / "arxiv-org-abs-2605-00055.md"
        self.twin = self.art / "arxiv-2605-00055.md"
        self.primary.write_text(_raw(ABS, "abstract only"))
        self.twin.write_text(_raw("https://arxiv.org/pdf/2605.00055v1", "full text " * 300))
        self.page = self.kb / f"wiki/format/papers/{PAGE}.md"
        self.page.write_text(
            f'---\ntitle: "{PAGE}"\nsource_type: "paper"\n'
            f'raw_path: "raw/papers/artifacts/arxiv-org-abs-2605-00055.md"\nurl: "{ABS}"\n'
            'date_added: 2026-06-04\nlast_updated: 2026-11-01\nrelated:\nsummary: "edited"\n---\n\n'
            '## Ambient Persuasion\n\nMy notes stay.\n')

    def run_cmd(self, *args, stdin="n"):
        import cmd_merge_raw  # type: ignore
        out = io.StringIO()
        with redirect_stdout(out), mock.patch("builtins.input", lambda *a: stdin):
            rc = cmd_merge_raw.handle(list(args), str(self.kb))
        return rc or 0, out.getvalue()


class MergeRaw(_Vault):
    def test_twin_joins_raw_paths_and_is_marked(self):
        body_before = self.page.read_text().split("\n---\n", 1)[1]
        twin_body_before = self.twin.read_text().split("\n---\n", 1)[1]
        rc, out = self.run_cmd("raw/papers/artifacts/arxiv-2605-00055.md", "--into", PAGE, "--yes")
        self.assertEqual(rc, 0, out)
        page = self.page.read_text()
        fm, body = page.split("\n---\n", 1)
        self.assertNotIn("\nraw_path:", fm)
        self.assertIn('raw_paths:\n  - "raw/papers/artifacts/arxiv-org-abs-2605-00055.md"\n'
                      '  - "raw/papers/artifacts/arxiv-2605-00055.md"', fm)
        self.assertEqual(body, body_before, "page body unchanged")
        twin = self.twin.read_text()
        self.assertIn(f'merged_into: "{PAGE}"', twin.split("\n---\n", 1)[0])
        self.assertEqual(twin.split("\n---\n", 1)[1], twin_body_before, "twin text unchanged")
        self.assertIn("kb merge-raw", (self.kb / ".athena/audit.jsonl").read_text())

    def test_page_name_with_glob_characters(self):
        # Real page: "arXiv: [2605.00055] Ambient Persuasion ..." -- brackets
        # are a glob character class, so an unescaped lookup said "not found".
        name = "arXiv: [2605.00055] Ambient Persuasion"
        self.page.rename(self.page.with_name(f"{name}.md"))
        rc, out = self.run_cmd("arxiv-2605-00055", "--into", name, "--yes")
        self.assertEqual(rc, 0, out)
        self.assertIn(f'merged_into: "{name}"', self.twin.read_text())

    def test_undo_restores_both(self):
        page0, twin0 = self.page.read_bytes(), self.twin.read_bytes()
        self.run_cmd("arxiv-2605-00055", "--into", PAGE, "--yes")
        import snapshot  # type: ignore
        ok, msg = snapshot.restore_last(str(self.kb))
        self.assertTrue(ok, msg)
        self.assertEqual(self.page.read_bytes(), page0)
        self.assertEqual(self.twin.read_bytes(), twin0)

    def test_declined_changes_nothing(self):
        page0 = self.page.read_bytes()
        rc, out = self.run_cmd("arxiv-2605-00055", "--into", PAGE, stdin="n")
        self.assertIn("Proceed?", out)
        self.assertEqual(self.page.read_bytes(), page0)

    def test_refusals(self):
        other = self.art / "other.md"
        other.write_text(_raw("https://arxiv.org/abs/2401.00001"))
        page0 = self.page.read_bytes()
        for args, why in ((("other", "--into", PAGE, "--yes"), "different source"),
                          (("arxiv-org-abs-2605-00055", "--into", PAGE, "--yes"), "already"),
                          (("arxiv-2605-00055", "--into", "No Such Page", "--yes"), "not found"),
                          (("nope", "--into", PAGE, "--yes"), "not found")):
            rc, out = self.run_cmd(*args)
            self.assertNotEqual(rc, 0, args)
            self.assertIn(why, out, args)
        self.assertEqual(self.page.read_bytes(), page0)


class WriterPrefersUnmergedButNeverTwins(unittest.TestCase):
    def test_all_merged_group_still_routes(self):
        import raw_writer  # type: ignore
        v = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, v, True)
        a = v / "raw/papers/artifacts"
        a.mkdir(parents=True)
        (a / "a.md").write_text(_raw(ABS, extra=f'merged_into: "{PAGE}"\n'))
        (a / "b.md").write_text(_raw(ABS, extra=f'merged_into: "{PAGE}"\n'))
        self.assertEqual(raw_writer.existing_raw_for(v, "paper", ABS), a / "a.md")
        (a / "c.md").write_text(_raw(ABS))
        self.assertEqual(raw_writer.existing_raw_for(v, "paper", ABS), a / "c.md", "unmerged preferred")


class BackfillOncePerGroup(_Vault):
    def setUp(self):
        super().setUp()
        import test_pdf_figures as PF
        if not PF.HAVE:
            self.skipTest("arcus >= 0.8.0 with PyMuPDF not installed")
        PF.make_pdf(self.art / "arxiv-2605-00055.pdf")       # only the twin has a PDF
        self.run_cmd("arxiv-2605-00055", "--into", PAGE, "--yes")

    def backfill(self, *args):
        import cmd_backfill_figures  # type: ignore
        out = io.StringIO()
        with redirect_stdout(out):
            rc = cmd_backfill_figures.handle(["--yes", *args], str(self.kb))
        return rc or 0, out.getvalue()

    def test_figures_go_on_the_member_with_the_pdf(self):
        rc, out = self.backfill()
        self.assertEqual(rc, 0, out)
        self.assertTrue((self.kb / "raw/assets/arxiv-2605-00055").is_dir(), out)
        self.assertFalse((self.kb / "raw/assets/arxiv-org-abs-2605-00055").exists())
        self.assertIn("../../assets/arxiv-2605-00055/figure-", self.twin.read_text())

    def test_two_pdfs_in_a_group_get_figures_once(self):
        import test_pdf_figures as PF
        PF.make_pdf(self.art / "arxiv-org-abs-2605-00055.pdf")  # now the primary has one too
        rc, out = self.backfill()
        self.assertEqual(rc, 0, out)
        dirs = sorted(p.name for p in (self.kb / "raw/assets").iterdir())
        self.assertEqual(dirs, ["arxiv-org-abs-2605-00055"], "first member in raw_paths order, once")
        self.assertIn("same paper as arxiv-org-abs-2605-00055", out)


if __name__ == "__main__":
    unittest.main()
