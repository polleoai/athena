"""Lint check #31 scope — issue #115 hardening.

Check #31 flags raw-frontmatter readers that use bare `fm.get('url')` instead
of `get_raw_source_url(fm)`. Canonical raws emit `source:`, not `url:`, so a
bare-`url:` reader silently misses every canonical raw (false orphans, broken
dedup). The check was originally scoped to only `bin/kb` + `kb_commands.py`, so
a new raw reader in any OTHER live module reintroduced the divergence uncaught.
The fix widens the scan to every `bin/lib/*.py`.

Two guarantees, two tests:
  1. NEGATIVE (permanent invariant): the live source tree has no bare raw
     `fm.get('url')` reader — the widened scan reports clean. This fails the
     day someone adds one anywhere in bin/lib, which is the whole point.
  2. POSITIVE: a planted raw-context reader inside the scanned set IS flagged,
     proving the widened file list actually reaches new modules.

Run from vault root:
    python3 -m pytest tests/test_lint_raw_url_reader_scope.py -v
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

# The exact name check #31 prints when it finds an offender (see _lint_body.py).
_CHECK_NAME = "Raw-frontmatter readers using direct fm.get('url')"


def _run_lint_capture(vault: Path) -> str:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        cmd_lint.handle([], str(vault))
    return buf.getvalue()


def _make_min_vault(tmp: Path) -> None:
    for d in ("wiki/format/webpages", "raw/webpages/artifacts", "inbox"):
        (tmp / d).mkdir(parents=True, exist_ok=True)


class TestLiveSourceHasNoRawUrlReader(unittest.TestCase):
    """Negative invariant — the real bin/lib stays clean under the widened scan."""

    def test_no_bare_raw_url_reader_in_bin_lib(self):
        tmp = Path(tempfile.mkdtemp(prefix="kb-rawurl-clean-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        # Symlink the whole bin so KB/bin/lib resolves to the REAL source tree.
        (tmp / "bin").symlink_to(ROOT / "bin")
        _make_min_vault(tmp)
        out = _run_lint_capture(tmp)
        self.assertNotIn(
            _CHECK_NAME, out,
            "check #31 flagged a bare raw fm.get('url') reader in live bin/lib — "
            "use get_raw_source_url(fm) instead (issue #115).",
        )


class TestPlantedReaderIsFlagged(unittest.TestCase):
    """Positive — a new raw reader in any scanned module is caught."""

    # `fm` (not `raw_fm`) so \bfm\.get matches; `raw_path` so the raw-context
    # gate (_RAW_FM_EXTRACT_RE) fires within the ±5-line window.
    _PLANTED = (
        "def read_source(raw_path):\n"
        "    fm, _ = extract_frontmatter(raw_path)\n"
        "    return fm.get('url')\n"
    )

    # A WIKI-context reader: variable `wiki_path` does NOT match the raw-context
    # gate (_RAW_FM_EXTRACT_RE), and wiki frontmatter legitimately uses `url:`.
    # The widened scan MUST NOT flag this — otherwise the natural "fix" would be
    # to re-narrow the file list, reopening #115.
    _PLANTED_WIKI = (
        "def read_wiki(wiki_path):\n"
        "    fm, _ = extract_frontmatter(wiki_path)\n"
        "    return fm.get('url')\n"
    )

    def _lint_with_planted_lib(self, filename: str, content: str) -> str:
        tmp = Path(tempfile.mkdtemp(prefix="kb-rawurl-planted-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        # Build a real bin/lib dir: symlink every real *.py (so the scan sees the
        # clean live set) + drop in one planted file. cmd_lint itself is imported
        # from the real LIB via sys.path, so it runs normally; only the #31
        # textual scan reads from this temp lib.
        lib = tmp / "bin" / "lib"
        lib.mkdir(parents=True)
        for f in LIB.glob("*.py"):
            (lib / f.name).symlink_to(f)
        (lib / filename).write_text(content, encoding="utf-8")
        _make_min_vault(tmp)
        return _run_lint_capture(tmp)

    def test_planted_raw_context_reader_flagged(self):
        out = self._lint_with_planted_lib("_planted_raw_reader_115.py", self._PLANTED)
        self.assertIn(_CHECK_NAME, out, "widened scan failed to flag the planted reader")
        self.assertIn("_planted_raw_reader_115.py", out)

    def test_planted_wiki_context_reader_not_flagged(self):
        out = self._lint_with_planted_lib("_planted_wiki_reader_115.py", self._PLANTED_WIKI)
        self.assertNotIn(
            _CHECK_NAME, out,
            "wiki-frontmatter reader (legitimate url:) was false-positived — the "
            "raw-context gate must keep the widened scan from flagging it.",
        )


if __name__ == "__main__":
    unittest.main()
