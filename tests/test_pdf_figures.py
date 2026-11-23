"""PDF figures land in the vault, embedded at their captions (arcus 0.8.0).

The user, 2026-11-22: "we should keep the figures." arcus 0.7.x dropped every
figure (arXiv 2611.20218 came out with 13 captions and no images). arcus 0.8.0
writes them as <slug>.figures/figure-NN-pP.png beside its markdown, references
them in place, and lists them in extractor_detail["figures"] (plus
["figures_skipped"] with a reason). Athena's part:

  1. Both PDF paths keep them: the direct-file path (arcus_file.ingest_local_file)
     and the arXiv paper path (capture._extract_pdf_fulltext).
  2. The PNGs are copied to raw/assets/<raw stem>/ (the layout webpage
     images use) with an _assets.json sidecar, and the raw's references
     point there (../../assets/<stem>/<file>), never at arcus's temp dir.
  3. What was skipped is said, not dropped: the body reports "N kept, M
     skipped (reason)".
  4. A figure path from arcus that escapes its output dir is refused.
  5. lint's orphan-asset check knows a paper's asset dir has an owner.

The fixture PDF is generated here (PyMuPDF): two real figures with captions
and one tiny decorative image. Offline.

Run from vault root:
    python3 -m pytest tests/test_pdf_figures.py -v
"""
from __future__ import annotations

import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from importlib.machinery import SourceFileLoader
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LIB = ROOT / "bin" / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

try:
    import fitz  # type: ignore  # PyMuPDF, via arcus's [pdf] extra
    from arcus.provider_runtime.types import FigureOptions  # type: ignore  # noqa: F401  (0.8.0+)
    HAVE = True
except ImportError:
    HAVE = False

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def _png(w, h, seed):
    """Random-noise pixels: a flat or simply patterned block reads as page
    background, not a figure, to pymupdf4llm."""
    import random
    rnd = random.Random(seed)
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, w, h), False)
    for y in range(0, h, 4):
        for x in range(0, w, 4):
            pix.set_rect(fitz.IRect(x, y, x + 4, y + 4),
                         (rnd.randrange(256), rnd.randrange(256), rnd.randrange(256)))
    return pix.tobytes("png")


def make_pdf(path: Path) -> None:
    # This exact layout is one pymupdf4llm writes images for (found by probing
    # on 11-22: smaller images, flat colours, or text kept clear of the image
    # area were all not written). One large noise figure per page.
    doc = fitz.open()
    for n, what in ((1, "a chart"), (2, "a diagram")):
        page = doc.new_page(width=612, height=792)
        page.insert_text((72, 72), f"Intro text of page {n}. " * 4, fontsize=10)
        page.insert_text((72, 330), f"Figure {n} Random noise that stands in for {what}.", fontsize=10)
        page.insert_image(fitz.Rect(50, 50, 560, 500), stream=_png(400, 300, n))
        page.insert_text((72, 540), "Body text after the figure. " * 4, fontsize=10)
        if n == 1:  # decorative: 12 pt square, under arcus's 40 pt min side
            page.insert_image(fitz.Rect(570, 20, 582, 32), stream=_png(24, 24, 9))
    doc.save(str(path))


def _load_capture():
    loader = SourceFileLoader("capture_figures_mod", str(LIB / "capture.py"))
    spec = importlib.util.spec_from_loader("capture_figures_mod", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


class _Vault(unittest.TestCase):
    def setUp(self):
        self.vault = Path(tempfile.mkdtemp(prefix="athena-fig-"))
        self.addCleanup(shutil.rmtree, self.vault, ignore_errors=True)
        (self.vault / "raw" / "papers" / "artifacts").mkdir(parents=True)
        (self.vault / "CLAUDE.md").write_text("# vault")
        self.pdf = self.vault / "raw" / "papers" / "artifacts" / "test-figures-paper.pdf"
        make_pdf(self.pdf)

    def assert_figures_in_vault(self, text: str, stem: str):
        adir = self.vault / "raw" / "assets" / stem
        pngs = sorted(adir.glob("*.png"))
        self.assertEqual(len(pngs), 2, f"the 2 real figures: {pngs}")
        for p in pngs:
            self.assertTrue(p.read_bytes().startswith(PNG_MAGIC), p)
        refs = re.findall(r"!\[[^\]]*\]\(([^)]+)\)", text)
        self.assertEqual(sorted(refs), sorted(f"../../assets/{stem}/{p.name}" for p in pngs), text[:400])
        self.assertNotIn(".figures/", text, "no reference into arcus's temp output")
        side = json.loads((adir / "_assets.json").read_text())
        self.assertEqual(side["page_slug"], stem)
        self.assertEqual(sorted(a["local"] for a in side["assets"]), [p.name for p in pngs])
        self.assertTrue(all("caption" in a and "page" in a for a in side["assets"]), "arcus fields carried")
        self.assertEqual(side["skipped"], [])


@unittest.skipUnless(HAVE, "arcus >= 0.8.0 with PyMuPDF not installed")
class DirectFilePath(_Vault):
    def test_figures_copied_embedded_and_skips_reported(self):
        import arcus_file  # type: ignore
        raw = arcus_file.ingest_local_file(
            vault=self.vault, url="https://example.org/test-figures-paper.pdf",
            local_path=self.pdf, source_type="paper", ext="pdf", title_hint="Test")
        text = Path(raw).read_text()
        stem = Path(raw).stem
        self.assertEqual(stem, "test-figures-paper")
        self.assert_figures_in_vault(text, stem)
        self.assertIn("**Figures:** 2 kept", text)
        # each image sits at its caption, before the caption text
        i1 = text.index("../../assets/test-figures-paper/figure-01")
        self.assertLess(i1, text.index("Figure 2 Random noise"))


@unittest.skipUnless(HAVE, "arcus >= 0.8.0 with PyMuPDF not installed")
class ArxivPaperPath(_Vault):
    def test_fulltext_carries_the_figures(self):
        cap = _load_capture()
        state = {}
        with redirect_stdout(io.StringIO()):
            text = cap._extract_pdf_fulltext(str(self.pdf), state, vault=str(self.vault),
                                             asset_slug="arxiv-0000-00000")
        self.assert_figures_in_vault(text, "arxiv-0000-00000")
        self.assertEqual(state.get("figures_note"), "2 kept")
        self.assertEqual(cap._fallback_extras(state), [], "figures are not a fallback")

    def test_paper_body_reports_the_figures(self):
        src = (LIB / "capture.py").read_text()
        i = src.index("def handle_paper")
        body = src[i:src.index("\ndef ", i + 10)]
        self.assertIn('_extract_pdf_fulltext(pdf_file, state, vault=KB_ROOT', body)
        self.assertIn('figures_note', body)


class SkipsAreReported(unittest.TestCase):
    """arcus lists what it skipped (too_small, max_figures, too_large,
    max_total_bytes, unreadable); Athena passes it on, never drops it.
    (A decorative image is below what pymupdf4llm even writes, so arcus's
    own skip list is fed here directly.)"""

    def test_note_and_sidecar_carry_arcus_skips(self):
        import arcus_file  # type: ignore
        out = Path(tempfile.mkdtemp())
        vault = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, out, True)
        self.addCleanup(shutil.rmtree, vault, True)
        (out / "x.figures").mkdir()
        (out / "x.figures" / "figure-01-p1.png").write_bytes(PNG_MAGIC + b"ok")
        payload = {"text": "![a](x.figures/figure-01-p1.png)\n",
                   "extractor_detail": {
                       "figures": [{"file": "x.figures/figure-01-p1.png", "page": 1, "caption": "a"}],
                       "figures_skipped": [{"page": 2, "reason": "max_figures", "caption": "b"},
                                           {"page": 3, "reason": "too_small", "caption": None}]}}
        text, kept, skipped = arcus_file._place_figures(out, payload, vault, "s")
        self.assertEqual(arcus_file.figures_note(kept, skipped),
                         "1 kept, 2 skipped (max_figures, too_small)")
        side = json.loads((vault / "raw/assets/s/_assets.json").read_text())
        self.assertEqual([x["reason"] for x in side["skipped"]], ["max_figures", "too_small"])

    def test_no_figures_no_note_no_dir(self):
        import arcus_file  # type: ignore
        out = Path(tempfile.mkdtemp())
        vault = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, out, True)
        self.addCleanup(shutil.rmtree, vault, True)
        text, kept, skipped = arcus_file._place_figures(out, {"text": "t", "extractor_detail": {}}, vault, "s")
        self.assertEqual((text, kept, skipped), ("t", [], []))
        self.assertEqual(arcus_file.figures_note(kept, skipped), "")
        self.assertFalse((vault / "raw" / "assets" / "s").exists())


class FigurePathSafety(unittest.TestCase):
    def test_a_path_outside_arcus_output_is_refused(self):
        import arcus_file  # type: ignore
        out = Path(tempfile.mkdtemp())
        vault = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, out, True)
        self.addCleanup(shutil.rmtree, vault, True)
        (out / "x.figures").mkdir()
        (out / "x.figures" / "figure-01-p1.png").write_bytes(PNG_MAGIC + b"ok")
        secret = out.parent / "athena-fig-secret.png"
        secret.write_bytes(PNG_MAGIC + b"secret")
        self.addCleanup(secret.unlink)
        payload = {"text": "![a](x.figures/figure-01-p1.png)\n![b](../athena-fig-secret.png)\n",
                   "extractor_detail": {"figures": [
                       {"file": "x.figures/figure-01-p1.png", "page": 1, "caption": "a"},
                       {"file": "../athena-fig-secret.png", "page": 1, "caption": "b"}],
                       "figures_skipped": []}}
        text, kept, skipped = arcus_file._place_figures(out, payload, vault, "s")
        self.assertEqual([k["local"] for k in kept], ["figure-01-p1.png"])
        self.assertFalse((vault / "raw" / "assets" / "s" / "athena-fig-secret.png").exists())
        self.assertNotIn("athena-fig-secret", text)
        self.assertEqual(skipped[-1]["reason"], "unsafe_path")


class LintKnowsPaperAssets(unittest.TestCase):
    def test_paper_asset_dir_is_not_an_orphan(self):
        d = Path(tempfile.mkdtemp(prefix="athena-fig-lint-"))
        self.addCleanup(shutil.rmtree, d, True)
        (d / "CLAUDE.md").write_text("# vault")
        (d / "RULES.md").write_text("# RULES\n")
        for sub in ("wiki/format/papers", "inbox", "raw/papers/artifacts", "raw/assets/p1",
                    "raw/assets/gone"):
            (d / sub).mkdir(parents=True, exist_ok=True)
        shutil.copytree(ROOT / "bin", d / "bin", ignore=shutil.ignore_patterns("__pycache__"))
        # p1 has its wiki page, so lint's orphan-raw pass leaves it alone.
        (d / "wiki/format/papers/P1.md").write_text(
            '---\ntitle: "P1"\nsource_type: "paper"\nraw_path: "raw/papers/artifacts/p1.md"\n'
            'url: "https://example.org/p1.pdf"\ndate_added: 2026-11-22\nlast_updated: 2026-11-22\n'
            'related:\nsummary: "s"\n---\n\n## P1\n\nbody\n')
        (d / "raw/papers/artifacts/p1.md").write_text(
            '---\ntitle: "P1"\nsource: "https://example.org/p1.pdf"\n---\n\n' + "text " * 200)
        for a in ("p1", "gone"):
            (d / "raw/assets" / a / "figure-01-p1.png").write_bytes(PNG_MAGIC)
        out = subprocess.run([sys.executable, str(d / "bin" / "kb"), "lint"], cwd=d,
                             capture_output=True, text=True, timeout=120,
                             env=dict(os.environ, KB_ROOT=str(d))).stdout
        self.assertIn("raw/assets/gone/", out, "a dir with no raw is still an orphan")
        self.assertNotIn("raw/assets/p1/", out, "a paper's figures have an owner")


if __name__ == "__main__":
    unittest.main()
