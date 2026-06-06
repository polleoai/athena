"""Regression tests for wiki_writer._safe_filename colon handling — issue #131.

The merge / rename / refresh-wiki path goes through wiki_writer.write_wiki,
which builds the on-disk filename via _safe_filename. That copy had drifted
from the canonical wiki_page._safe_filename: it dropped ':' → '-'
unconditionally, so a merge produced 'Web- Foo.md' while a fresh capture of
the same source produced 'Web: Foo.md'. Since Obsidian uses the filename as the
wikilink target, the divergence broke link resolution.

Fix: mirror wiki_page._safe_filename's platform-aware colon rule — POSIX keeps
':' verbatim (Athena's 'Prefix: Title' convention), Windows maps ':' → ' —'.

Run from vault root:
    python3 -m unittest tests.test_wiki_writer_safe_filename -v
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

_VAULT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_VAULT / "bin" / "lib"))

import wiki_page  # noqa: E402
import wiki_writer  # noqa: E402


class TestSafeFilenameColon(unittest.TestCase):
    @mock.patch.object(wiki_writer.os, "name", "posix")
    def test_posix_keeps_colon(self):
        """The exact #131 symptom: the prefix colon must survive on POSIX."""
        self.assertEqual(wiki_writer._safe_filename("Web: Foo"), "Web: Foo")
        self.assertEqual(
            wiki_writer._safe_filename("GitHub: owner/repo".replace("/", "-")),
            "GitHub: owner-repo",
        )

    @mock.patch.object(wiki_writer.os, "name", "nt")
    def test_windows_maps_colon_to_emdash(self):
        """On Windows ':' is reserved → ' —', matching fresh-capture output."""
        self.assertEqual(wiki_writer._safe_filename("Web: Foo"), "Web — Foo")
        # No stray ':' may survive on Windows.
        self.assertNotIn(":", wiki_writer._safe_filename("X: a: b"))

    @mock.patch.object(wiki_writer.os, "name", "posix")
    def test_other_reserved_chars_still_hyphenated(self):
        """Only the colon behavior changed; other reserved chars still → '-'."""
        self.assertEqual(wiki_writer._safe_filename('a/b\\c*d?e"f<g>h|i'),
                         "a-b-c-d-e-f-g-h-i")

    @mock.patch.object(wiki_writer.os, "name", "posix")
    def test_unescapes_and_collapses_whitespace(self):
        """Pre-existing behavior preserved (no regression beyond the colon)."""
        self.assertEqual(wiki_writer._safe_filename("A &amp;  B"), "A & B")
        self.assertEqual(wiki_writer._safe_filename("trailing.  "), "trailing")


class TestParityWithWikiPage(unittest.TestCase):
    """The invariant #131 is actually about: for the canonical 'Prefix: Title'
    shape, the merge/rename writer (wiki_writer) and the fresh-capture writer
    (wiki_page) must produce byte-identical filenames on every platform — else
    the same source gets two filenames and wikilinks break. This guards against
    silent future drift (e.g. someone changing one side's em-dash separator).

    Shapes here deliberately exclude / \\ * ? " < > | because the two functions
    intentionally diverge on those (documented); the colon-bearing canonical
    titles are what must stay in lockstep.
    """

    _SHAPES = [
        "Web: Foo",
        "GitHub: owner-repo",
        "X: a: b",
        "arXiv: Some Paper Title",
        "LinkedIn: After Mythos — Defending at Machine Speed",
    ]

    def _assert_parity_for(self, osname: str):
        with mock.patch.object(wiki_writer.os, "name", osname), \
             mock.patch.object(wiki_page.os, "name", osname):
            for s in self._SHAPES:
                self.assertEqual(
                    wiki_writer._safe_filename(s),
                    wiki_page._safe_filename(s),
                    f"merge/fresh-capture filename diverged for {s!r} on {osname}",
                )

    def test_parity_posix(self):
        self._assert_parity_for("posix")

    def test_parity_windows(self):
        self._assert_parity_for("nt")


if __name__ == "__main__":
    unittest.main()
