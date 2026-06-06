"""Regression tests for bin/ingest-file slug generation — issue #126.

The bug: `_slugify` hard-capped slugs at 60 chars and cut mid-word, so a
file URL with no PDF metadata produced a slug like
`us-17-robbins-an-ace-up-the-sleeve-designing-active-director` — the
meaningful tail (`...directory-dacl-backdoors`) silently dropped, and the
later #125 conference/acronym title fixes could not recover it because they
run *after* slugging.

The fix:
  1. The cap is config-driven via `naming.slug_max_chars` (default 100),
     mirroring `naming.filename_max_chars` so the slug isn't clipped tighter
     than the wiki filename it feeds.
  2. When truncation IS needed, the cut lands on the last hyphen boundary so
     the tail is a complete word, not a mangled fragment.

Run from vault root:
    python3 -m unittest tests.test_ingest_file_slugify -v
"""

from __future__ import annotations

import importlib.util
import sys
import unittest
from importlib.machinery import SourceFileLoader
from pathlib import Path

# bin/ingest-file imports url_detect / arcus_file from bin/lib; make sure
# they resolve the same way the script does when invoked as a subprocess.
_VAULT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_VAULT / "bin" / "lib"))


def _load_ingest_file():
    """Load bin/ingest-file (extensionless script) as an importable module."""
    path = _VAULT / "bin" / "ingest-file"
    loader = SourceFileLoader("ingest_file_mod", str(path))
    spec = importlib.util.spec_from_loader("ingest_file_mod", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


_INGEST = _load_ingest_file()

# The exact example from issue #126 — a BlackHat slide deck whose URL basename
# is the only title source (PDF metadata empty).
_BLACKHAT_SLUG = "us-17-robbins-an-ace-up-the-sleeve-designing-active-directory-dacl-backdoors"
_BLACKHAT_URL = (
    "https://www.blackhat.com/docs/us-17/thursday/"
    "us-17-Robbins-An-ACE-Up-The-Sleeve-Designing-Active-Directory-DACL-Backdoors.pdf"
)


class TestSlugMaxChars(unittest.TestCase):
    def test_cap_reads_config_default_100(self):
        self.assertEqual(_INGEST._slug_max_chars(), 100)


class TestSlugifyTailPreserved(unittest.TestCase):
    def test_blackhat_tail_no_longer_dropped(self):
        """The 75-char BlackHat slug is under the 100 cap → preserved whole.
        Under the old 60-char cap this lost `directory-dacl-backdoors`."""
        slug = _INGEST._slugify(
            "us-17 Robbins An ACE Up The Sleeve Designing Active Directory DACL Backdoors"
        )
        self.assertEqual(slug, _BLACKHAT_SLUG)
        self.assertTrue(slug.endswith("dacl-backdoors"))
        # Guard against a silent regression back to the 60-char cut.
        self.assertGreater(len(slug), 60)

    def test_slug_from_url_recovers_full_tail(self):
        """End-to-end via the URL basename path the issue actually hit."""
        slug = _INGEST._slug_from_url(_BLACKHAT_URL)
        self.assertEqual(slug, _BLACKHAT_SLUG)


class TestSlugifyWordBoundaryCut(unittest.TestCase):
    def test_truncation_cuts_on_word_boundary(self):
        """A slug longer than the cap is cut at a hyphen, never mid-word."""
        tokens = [
            "alpha", "bravo", "charlie", "delta", "echo", "foxtrot",
            "golf", "hotel", "india", "juliet", "kilo", "lima", "mike",
            "november", "oscar", "papa", "quebec", "romeo", "sierra",
        ]
        title = " ".join(tokens)  # well over 100 chars
        slug = _INGEST._slugify(title, max_len=100)
        self.assertLessEqual(len(slug), 100)
        result_tokens = slug.split("-")
        # Every token in the result must be a *complete* token from the input,
        # in order — i.e. the result is a clean prefix, no fragment tail.
        self.assertEqual(result_tokens, tokens[: len(result_tokens)])

    def test_early_boundary_ignored_falls_back_to_hard_cut(self):
        """A short leading word followed by a very long token: the only hyphen
        boundary sits before max_len//2, so the word-boundary cut is ignored and
        we hard-cut — rather than collapsing to the 5-char 'alpha' tail. This is
        the branch the code comment singles out."""
        slug = _INGEST._slugify("alpha " + "x" * 100, max_len=100)
        self.assertEqual(len(slug), 100)
        self.assertTrue(slug.startswith("alpha-x"))
        self.assertNotEqual(slug, "alpha")

    def test_single_overlong_token_still_truncates(self):
        """A slug whose first token alone exceeds the cap can't honor a word
        boundary; it falls back to a hard cut rather than collapsing to ''."""
        slug = _INGEST._slugify("x" * 200, max_len=100)
        self.assertEqual(len(slug), 100)
        self.assertEqual(slug, "x" * 100)

    def test_empty_input_is_untitled(self):
        self.assertEqual(_INGEST._slugify(""), "untitled")
        self.assertEqual(_INGEST._slugify("!!!@@@"), "untitled")


if __name__ == "__main__":
    unittest.main()
