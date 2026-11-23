"""One source, one raw: a second capture path must not write a twin.

Found 2026-11-22 while backfilling paper figures: 5 arXiv papers had two
raws each with the SAME source URL, one backing the wiki page and one with
no page at all.
  - The slug scheme changed between captures: arxiv-2312.00752.md (May, the
    file name of the downloaded PDF) vs arxiv-2312-00752.md (October, the
    paper path), so the "already exists" check looked for the new name only.
  - Or two capturers raced: the plugin's browser capture and the CLI took
    arxiv.org/abs/2605.00055 18 s apart (arxiv-org-abs-2605-00055.md and
    arxiv-2605-00055.md).

Rules:
  1. raw_writer.write_raw to a NEW path for a source that an existing raw
     of that category already records (canonical URL, so /pdf/<id>v2 ==
     /abs/<id>) writes to the existing raw instead, and says so.
  2. An unmerged raw is preferred over one marked merged_into (if all are
     merged, the first merged one is used, never a new twin).
  3. A different source with a colliding slug still gets <slug>-2 (unchanged).
  4. capture's "already exists" check finds the existing raw by source too.
  5. lint lists same-source raws that are not merged (gated, oracle parity).

Run from vault root:
    python3 -m pytest tests/test_same_source_twins.py -v
"""
from __future__ import annotations

import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LIB = ROOT / "bin" / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

import raw_writer  # type: ignore  # noqa: E402

ABS = "https://arxiv.org/abs/2312.00752"
PDF = "https://arxiv.org/pdf/2312.00752v2"
BODY = "## Full Text\n\n" + "Mamba text. " * 50


class WriterRoutesTwins(unittest.TestCase):
    def setUp(self):
        self.vault = Path(tempfile.mkdtemp(prefix="athena-twins-"))
        self.addCleanup(shutil.rmtree, self.vault, ignore_errors=True)
        self.art = self.vault / "raw" / "papers" / "artifacts"

    def write(self, url, slug=None, body=BODY):
        err = io.StringIO()
        with redirect_stderr(err):
            p = raw_writer.write_raw(vault_root=self.vault, source_type="paper", url=url,
                                     title="Mamba", body=body, slug_override=slug)
        return Path(p), err.getvalue()

    def test_pdf_path_after_arxiv_path_reuses_the_raw(self):
        # (write_raw normalizes "." to "-" in a slug, so today's twins come
        # from a derived slug against an explicit one; the vault's dotted
        # names were written by an older writer.)
        first, _ = self.write(ABS, slug="arxiv-2312-00752")
        second, err = self.write(PDF, slug="arxiv-org-abs-2312-00752")
        self.assertEqual(second, first)
        self.assertEqual(sorted(p.name for p in self.art.glob("*.md")), ["arxiv-2312-00752.md"])
        self.assertIn("already captured as", err)

    def test_generic_slug_after_pdf_slug_reuses_the_raw(self):
        first, _ = self.write(ABS, slug="arxiv-2312-00752")
        second, _ = self.write(ABS)  # the derived slug, e.g. arxiv-org-abs-2312-00752
        self.assertEqual(second, first)
        self.assertEqual(len(list(self.art.glob("*.md"))), 1)

    def test_a_raw_recording_a_non_canonical_url_is_found(self):
        # Older writers and the plugin record what they were given, e.g. the
        # PDF URL with a version: the comparison canonicalizes BOTH sides.
        self.art.mkdir(parents=True, exist_ok=True)
        old = self.art / "arxiv-2312.00752.md"
        old.write_text(f'---\ntitle: "Mamba"\nsource: "{PDF}"\n---\n\n' + "old text " * 80)
        out, _ = self.write(ABS, slug="arxiv-org-abs-2312-00752")
        self.assertEqual(out, old)

    def test_merged_twin_is_not_the_target_when_an_unmerged_one_exists(self):
        primary, _ = self.write(ABS, slug="mamba-primary")
        twin = self.art / "arxiv-2312-00752.md"  # sorts before mamba-primary
        twin.write_text(f'---\ntitle: "Mamba"\nsource: "{ABS}"\nmerged_into: "[[Mamba]]"\n---\n\nold\n')
        out, _ = self.write(PDF, slug="arxiv-org-abs-2312-00752")
        self.assertEqual(out, primary)

    def test_a_different_source_still_gets_its_own_file(self):
        self.write(ABS, slug="arxiv-2312-00752")
        other, _ = self.write("https://arxiv.org/abs/2401.00001", slug="arxiv-2312-00752")
        self.assertEqual(other.name, "arxiv-2312-00752-2.md")

    def test_lookup_helper_for_capture(self):
        first, _ = self.write(ABS, slug="arxiv-2312-00752")
        self.assertEqual(raw_writer.existing_raw_for(self.vault, "paper", PDF), first)
        self.assertIsNone(raw_writer.existing_raw_for(self.vault, "paper", "https://arxiv.org/abs/2401.00001"))


class CaptureFindsBySource(unittest.TestCase):
    def test_every_handler_checks_by_source(self):
        src = (LIB / "capture.py").read_text()
        for h, st in (("handle_paper", "paper"), ("handle_webpage", "webpage"),
                      ("handle_repo", "repo"), ("handle_video", "video")):
            i = src.index(f"def {h}(")
            self.assertIn(f'_existing_by_source(raw_file, url, "{st}")', src[i:i + 400], h)
        self.assertIn("existing_raw_for(", src[src.index("def _existing_by_source"):][:1200])


class LintListsTwins(unittest.TestCase):
    def _vault(self, merged: bool):
        d = Path(tempfile.mkdtemp(prefix="athena-twins-lint-"))
        self.addCleanup(shutil.rmtree, d, True)
        (d / "CLAUDE.md").write_text("# vault")
        (d / "RULES.md").write_text("# RULES\n")
        for sub in ("wiki/format/papers", "inbox", "raw/papers/artifacts"):
            (d / sub).mkdir(parents=True, exist_ok=True)
        shutil.copytree(ROOT / "bin", d / "bin", ignore=shutil.ignore_patterns("__pycache__"))
        a = d / "raw/papers/artifacts"
        (a / "arxiv-2312.00752.md").write_text(f'---\ntitle: "Mamba"\nsource: "{ABS}"\n---\n\n' + "text " * 200)
        extra = 'merged_into: "[[Mamba]]"\n' if merged else ""
        (a / "arxiv-2312-00752.md").write_text(f'---\ntitle: "Mamba"\nsource: "{PDF}"\n{extra}---\n\n' + "text " * 200)
        (d / "wiki/format/papers/Mamba.md").write_text(
            '---\ntitle: "Mamba"\nsource_type: "paper"\nraw_path: "raw/papers/artifacts/arxiv-2312.00752.md"\n'
            f'url: "{ABS}"\ndate_added: 2026-11-22\nlast_updated: 2026-11-22\nrelated:\nsummary: "s"\n---\n\nbody\n')
        return d

    def _lint(self, d):
        return subprocess.run([sys.executable, str(d / "bin" / "kb"), "lint"], cwd=d, capture_output=True,
                              text=True, timeout=120, env=dict(os.environ, KB_ROOT=str(d))).stdout

    def test_unmerged_twins_listed(self):
        out = self._lint(self._vault(merged=False))
        self.assertIn("Same source captured twice", out)
        self.assertIn("arxiv-2312-00752.md", out)

    def test_merged_twins_not_listed(self):
        self.assertNotIn("Same source captured twice", self._lint(self._vault(merged=True)))


if __name__ == "__main__":
    unittest.main()
