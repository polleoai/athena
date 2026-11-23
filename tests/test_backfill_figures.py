"""`kb backfill-figures`: give existing papers their PDF figures, in place.

The user, 2026-11-22: re-capture "All papers" for figures (arcus 0.8.0).
kb delete + kb add would regenerate every wiki page, and 15 of the 53 paper
pages carry later edits, so the user chose an in-place command instead,
modelled on kb backfill-assets:

  1. Only raws with a LOCAL PDF are touched; the rest are skipped and listed.
     The first output line gives the counts.
  2. Each kept figure is copied to raw/assets/<stem>/ and INSERTED into the
     raw just before its caption line; unmatched figures go to a closing
     "## Figures" section. A "**Figures:**" line is added. Nothing in the
     raw is removed or reordered (grow-only), and wiki pages are untouched.
  3. One snapshot batch per run: `kb undo` restores the raws and removes the
     asset dirs. Without --yes it asks Proceed? first; --dry-run changes
     nothing.
  4. A raw that already has its figures is left alone (re-runs are safe).

Run from vault root:
    python3 -m pytest tests/test_backfill_figures.py -v
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

import test_pdf_figures as PF  # noqa: E402  (the fixture PDF arcus extracts 2 figures from)

FULL_TEXT = ("Intro text of page 1.\n\nFigure 1 Random noise that stands in for a chart.\n\n"
             "Body text after the figure.\n\nIntro text of page 2.\n\n"
             "Figure 2 Random noise that stands in for a diagram.\n\nBody text after the figure.\n")


def _raw(stem: str, pdf_line: bool = True) -> str:
    return (f'---\ntitle: "Paper {stem}"\nsource_type: "paper"\nsource: "https://arxiv.org/abs/{stem}"\n'
            f'captured_at: "2026-10-07T00:00:00Z"\n---\n\n'
            f'- **Authors:** Someone\n' + (f'- **PDF:** ./{stem}.pdf\n' if pdf_line else '')
            + f'- **Clipped:** 2026-10-07\n- **Content fetched:** yes (abstract + full text via arcus)\n\n'
              f'## Abstract\n\nAn abstract.\n\n## Full Text\n\n{FULL_TEXT}')


@unittest.skipUnless(PF.HAVE, "arcus >= 0.8.0 with PyMuPDF not installed")
class Backfill(unittest.TestCase):
    def setUp(self):
        self.kb = Path(tempfile.mkdtemp(prefix="athena-bf-"))
        self.addCleanup(shutil.rmtree, self.kb, ignore_errors=True)
        (self.kb / "bin").symlink_to(ROOT / "bin")
        for d in ("wiki/format/papers", "raw/papers/artifacts", "inbox"):
            (self.kb / d).mkdir(parents=True, exist_ok=True)
        art = self.kb / "raw/papers/artifacts"
        self.raw = art / "paper-a.md"
        self.raw.write_text(_raw("paper-a"))
        PF.make_pdf(art / "paper-a.pdf")
        (art / "paper-b.md").write_text(_raw("paper-b", pdf_line=False))   # no local PDF
        self.page = self.kb / "wiki/format/papers/Paper A.md"
        self.page.write_text('---\ntitle: "Paper A"\nraw_path: "raw/papers/artifacts/paper-a.md"\n'
                             'summary: "hand-edited summary"\n---\n\nMy own notes.\n')
        self.before_raw = self.raw.read_bytes()
        self.before_page = self.page.read_bytes()
        import cmd_backfill_figures  # type: ignore
        self.cmd = cmd_backfill_figures

    def run_cmd(self, *args, stdin="n\n"):
        out = io.StringIO()
        with redirect_stdout(out), mock.patch("builtins.input", lambda *a: stdin.strip()):
            rc = self.cmd.handle(list(args), str(self.kb))
        return rc or 0, out.getvalue()

    def test_dry_run_counts_first_and_changes_nothing(self):
        rc, out = self.run_cmd("--dry-run", stdin="y\n")  # even a "y" must not apply it
        self.assertEqual(rc, 0, out)
        first = out.strip().splitlines()[0]
        self.assertIn("2 papers: 1 with a local PDF, 1 skipped (no local PDF)", first)
        self.assertIn("paper-b", out)
        self.assertEqual(self.raw.read_bytes(), self.before_raw)
        self.assertFalse((self.kb / "raw/assets").exists())

    def test_apply_inserts_figures_grow_only_and_keeps_the_wiki(self):
        rc, out = self.run_cmd("--yes")
        self.assertEqual(rc, 0, out)
        pngs = sorted((self.kb / "raw/assets/paper-a").glob("figure-*.png"))
        self.assertEqual(len(pngs), 2, out)
        new = self.raw.read_text()
        for p in pngs:
            self.assertIn(f"](../../assets/paper-a/{p.name})", new)
        self.assertIn("- **Figures:** 2 kept", new)
        old_lines = self.before_raw.decode().splitlines()
        it = iter(new.splitlines())
        self.assertTrue(all(any(o == n for n in it) for o in old_lines),
                        "every original line is still there, in order (grow-only)")
        self.assertEqual(self.page.read_bytes(), self.before_page, "wiki page untouched")
        self.assertIn("figures added: 2", out)

    def test_undo_reverts_the_run(self):
        self.run_cmd("--yes")
        import snapshot  # type: ignore
        ok, msg = snapshot.restore_last(str(self.kb))
        self.assertTrue(ok, msg)
        self.assertEqual(self.raw.read_bytes(), self.before_raw)
        self.assertFalse((self.kb / "raw/assets/paper-a").exists())

    def test_rerun_is_a_no_op(self):
        self.run_cmd("--yes")
        after = self.raw.read_bytes()
        rc, out = self.run_cmd("--yes")
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.raw.read_bytes(), after)
        self.assertIn("already have figures", out)

    def test_a_paper_without_figures_is_marked_and_not_redone(self):
        import cmd_backfill_figures as bf  # type: ignore
        with mock.patch.object(bf, "insert_figures", side_effect=AssertionError("not reached")), \
             mock.patch("arcus_file._extract_via_arcus",
                        lambda *a, **k: {"figures": [], "figures_skipped": [{"page": 1, "reason": "too_small"}]}):
            rc, out = self.run_cmd("--yes")
        self.assertEqual(rc, 0, out)
        self.assertIn("- **Figures:** 0 kept, 1 skipped (too_small)", self.raw.read_text())
        rc, out = self.run_cmd("--dry-run", stdin="y\n")
        self.assertIn("1 already have figures", out.splitlines()[0])

    def test_marker_without_a_content_fetched_line(self):
        # ai-implications-cybersecurity had no "Content fetched" line: the
        # marker went nowhere, the report said "marked" anyway, and the next
        # run re-extracted it (batch 2, 2026-11-22).
        self.raw.write_text(self.raw.read_text().replace(
            "- **Content fetched:** yes (abstract + full text via arcus)\n", ""))
        with mock.patch("arcus_file._extract_via_arcus",
                        lambda *a, **k: {"figures": [], "figures_skipped": []}):
            rc, out = self.run_cmd("--yes")
        self.assertEqual(rc, 0, out)
        text = self.raw.read_text()
        self.assertIn("- **Figures:** 0 kept", text)
        fm_end = text.index("\n---\n", 3) + 5
        self.assertTrue(text[fm_end:].lstrip("\n").startswith("- **Figures:** 0 kept"), "right after the frontmatter")
        rc, out = self.run_cmd("--dry-run", stdin="y\n")
        self.assertIn("1 already have figures", out.splitlines()[0])

    def test_declined_changes_nothing(self):
        rc, out = self.run_cmd(stdin="n\n")
        self.assertIn("Proceed?", out)
        self.assertEqual(self.raw.read_bytes(), self.before_raw)
        self.assertFalse((self.kb / "raw/assets").exists())


class PanelsShareACaption(unittest.TestCase):
    def test_panels_with_one_caption_all_sit_before_it(self):
        # Mamba-3 (2026-11-22): arcus gave two panel images the same
        # "Figure 1: Left: ..." caption; the second went to ## Figures.
        import cmd_backfill_figures as bf  # type: ignore
        cap = "Figure 1: Left: The structured mask induced by the exponential-trapezoidal rule"
        body = f"Intro.\n\n{cap} (Section 3.1).\n\nMore.\n"
        kept = [{"local": "figure-01-p2.png", "caption": cap}, {"local": "figure-02-p2.png", "caption": cap}]
        out = bf.insert_figures(body, kept, "s")
        self.assertNotIn("## Figures", out)
        i_cap = out.index(f"\n{cap} (Section")
        self.assertLess(out.index("figure-01-p2.png"), i_cap)
        self.assertLess(out.index("figure-02-p2.png"), i_cap)


class GrowOnlyGuard(unittest.TestCase):
    """The last check before a raw is written: every original line survives,
    in order. insert_figures never breaks it; the guard is for any change
    that would."""

    def test_guard(self):
        import cmd_backfill_figures as bf  # type: ignore
        old = "a\nb\nc\n"
        self.assertTrue(bf._grow_only(old, "a\n![x](y)\nb\nc\n"))
        self.assertFalse(bf._grow_only(old, "a\nc\n![x](y)\n"), "a dropped line")
        self.assertFalse(bf._grow_only(old, "b\na\nc\n![x](y)\n"), "reordered")
        self.assertFalse(bf._grow_only(old, old), "nothing added is not a backfill")


class InsertAtCaptions(unittest.TestCase):
    def test_before_the_caption_else_a_figures_section(self):
        import cmd_backfill_figures as bf  # type: ignore
        body = "Intro.\n\nFigure 1 The same Click requirement presented directly.\n\nMore.\n"
        kept = [{"local": "figure-01-p3.png", "caption": "Figure 1 The same Click requirement presented directly as an issue"},
                {"local": "figure-02-p7.png", "caption": "Figure 9 Nowhere in this text"},
                {"local": "figure-03-p8.png", "caption": None}]
        out = bf.insert_figures(body, kept, "s")
        i_ref = out.index("](../../assets/s/figure-01-p3.png)")
        i_cap = out.index("\nFigure 1 The same Click")  # the caption line (the alt text repeats it)
        self.assertLess(i_ref, i_cap, "the image sits right before its caption")
        self.assertIn("## Figures", out)
        tail = out[out.index("## Figures"):]
        self.assertIn("figure-02-p7.png", tail)
        self.assertIn("figure-03-p8.png", tail)
        self.assertTrue(all(line in out.splitlines() for line in body.splitlines()))


if __name__ == "__main__":
    unittest.main()
