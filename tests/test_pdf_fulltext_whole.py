"""A long PDF comes back whole through Athena's PDF path (arcus#18).

arcus's pymupdf4llm and pdftotext tiers cut PDF text at 50,000 characters,
silently: Athena's paper raws were ~52k where the papers are 100-144k
(2111.00396: 53k of 103k; Mamba-3: 51k of 115k; Mamba: 52k of 144k), found
2026-11-22. The fix (arcus#18) removes the cap on the PDF tiers.

This generates a ~108k-character PDF and runs it through
arcus_file._extract_via_arcus, the call both of Athena's PDF paths use, then
checks that the text is whole: its last line is there and its length is
close to what was written. Red on arcus <= 0.8.1; it is listed in
tests/known-failures.txt until Athena's pin moves past the fix, and
scripts/loop-verify-pip.sh runs it against arcus fix branches.

Run from vault root:
    python3 -m pytest tests/test_pdf_fulltext_whole.py -v
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

try:
    import fitz  # type: ignore
    import arcus.provider_runtime  # type: ignore  # noqa: F401
    HAVE = True
except ImportError:
    HAVE = False

END = "END-OF-DOCUMENT-MARKER-7f3a"


def make_long_pdf(path: Path) -> int:
    """~108k characters over 16 pages of unique lines; returns chars written."""
    doc = fitz.open()
    written = 0
    n = 0
    for _page in range(16):
        page = doc.new_page(width=612, height=792)
        y = 40
        for _ in range(90):
            n += 1
            line = f"Line {n:05d} of a long test paper about whole-text extraction, sentence {n * 7 % 997}."
            page.insert_text((36, y), line, fontsize=7)
            written += len(line) + 1
            y += 8
    page.insert_text((36, y + 8), END, fontsize=7)
    doc.save(str(path))
    return written + len(END)


@unittest.skipUnless(HAVE, "arcus with PyMuPDF not installed")
class WholeText(unittest.TestCase):
    def test_a_108k_char_pdf_is_not_cut(self):
        import arcus_file  # type: ignore
        d = Path(tempfile.mkdtemp(prefix="athena-whole-"))
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        pdf = d / "long.pdf"
        written = make_long_pdf(pdf)
        self.assertGreater(written, 100_000)
        text = arcus_file._extract_via_arcus(pdf, "pdf")["text"]
        self.assertIn(END, text, f"the last line is missing: got {len(text)} of ~{written} chars")
        self.assertGreater(len(text), 0.9 * written, f"{len(text)} of ~{written} chars")


if __name__ == "__main__":
    unittest.main()
