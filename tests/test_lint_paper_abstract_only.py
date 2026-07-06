"""Lint surfacing: a paper raw whose body is abstract-only while its PDF sits on
disk must be flagged for re-capture (so the full text can be extracted).

The arxiv `paper` branch used to write only the HTML-scraped abstract even after
downloading the PDF, so the wiki summary + search index saw a fraction of the
paper (witnessed: arxiv 2606.21071, Local LLM Agents as Vulnerable Runtimes,
2026-07-04). handle_paper now runs the PDF through arcus and appends a
`## Full Text` section. This lint check SURFACES the back-catalog captured
before that fix — it does not auto-fix, because re-capture is a network
operation (discover-and-surface).

Run from vault root:
    python3 -m pytest tests/test_lint_paper_abstract_only.py -v
"""
from __future__ import annotations

import contextlib
import io
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LIB = ROOT / "bin" / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

import cmd_lint  # type: ignore  # noqa: E402

_MSG = "Papers with PDF but abstract-only body"


def _paper_raw(*, pdf_line: bool, full_text: bool) -> str:
    # Substantial body so the thin-orphan check doesn't trash the raw before
    # the paper check runs.
    abstract = "\n\n".join(
        f"Abstract sentence {i} describing the contribution in enough detail "
        f"to clear the thin-content bar that runs before this check."
        for i in range(1, 6)
    )
    parts = [
        "---",
        'title: "A Paper About Widgets"',
        'source: "https://arxiv.org/abs/2606.21071"',
        'clipped_via: "kb-capture"',
        "---",
        "",
        "# A Paper About Widgets",
        "",
        "- **Authors:** Ada Lovelace",
    ]
    if pdf_line:
        parts.append("- **PDF:** ./arxiv-2606.21071.pdf")
    parts += ["- **Clipped:** 2026-07-04", "", "## Abstract", "", abstract]
    if full_text:
        body = "\n\n".join(
            f"Full-text section {i} with the complete paper content extracted "
            f"from the PDF, well beyond the abstract."
            for i in range(1, 8)
        )
        parts += ["", "## Full Text", "", body]
    return "\n".join(parts) + "\n"


class PaperAbstractOnlyLint(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kb-paper-lint-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        (self.tmp / "bin").symlink_to(ROOT / "bin")
        for d in ("wiki/format/papers", "raw/papers/artifacts", "inbox"):
            (self.tmp / d).mkdir(parents=True, exist_ok=True)

    def _write(self, name: str, content: str, *, pdf: bool) -> None:
        raw = self.tmp / "raw/papers/artifacts" / name
        raw.write_text(content, encoding="utf-8")
        if pdf:
            (self.tmp / "raw/papers/artifacts/arxiv-2606.21071.pdf").write_bytes(
                b"%PDF-1.4\n" + b"0" * 2048)

    def _lint_output(self) -> str:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cmd_lint.handle([], str(self.tmp))
        return buf.getvalue()

    def test_abstract_only_with_pdf_is_surfaced(self):
        self._write("arxiv-2606-21071.md",
                    _paper_raw(pdf_line=True, full_text=False), pdf=True)
        out = self._lint_output()
        self.assertIn(_MSG, out)
        self.assertIn("arxiv-2606-21071.md", out)

    def test_full_text_present_is_not_surfaced(self):
        self._write("arxiv-2606-21071.md",
                    _paper_raw(pdf_line=True, full_text=True), pdf=True)
        out = self._lint_output()
        self.assertNotIn(_MSG, out)

    def test_pdf_missing_on_disk_is_not_surfaced(self):
        # PDF line present but no sibling file → an asset problem, not this one.
        self._write("arxiv-2606-21071.md",
                    _paper_raw(pdf_line=True, full_text=False), pdf=False)
        out = self._lint_output()
        self.assertNotIn(_MSG, out)

    def test_no_pdf_line_is_not_surfaced(self):
        # No local PDF at all → full-text re-capture is not applicable.
        self._write("arxiv-2606-21071.md",
                    _paper_raw(pdf_line=False, full_text=False), pdf=False)
        out = self._lint_output()
        self.assertNotIn(_MSG, out)


if __name__ == "__main__":
    unittest.main()
