"""Regression guards for title-derivation fixes that shipped without tests.

Both behaviors below were already fixed on main (verified during the 2026-06-05
backlog triage) but had zero dedicated coverage, so a future refactor could
silently regress them. These lock them in.

  #125 — apply_naming_convention on a conference PDF URL must: apply the
         conference prefix (BlackHat USA 2017), restore acronyms (ACE/DACL, not
         Ace/Dacl), and NOT clip the tail at the old hardcoded 65-char cap
         ('...Backdoors' must survive).
  #116 — build_fallback_data, when the H1 is a generic filtered title ('Tweet'),
         must skip a leading `source:`-style frontmatter-pair line during its
         body scan instead of promoting it to the title.

Run from vault root:
    python3 -m pytest tests/test_title_derivation_regression.py -v
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

_VAULT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_VAULT / "bin" / "lib"))

from wiki_page import apply_naming_convention, build_fallback_data  # noqa: E402

_BLACKHAT_URL = (
    "https://www.blackhat.com/docs/us-17/thursday/"
    "us-17-Robbins-An-ACE-Up-The-Sleeve-Designing-Active-Directory-DACL-Backdoors.pdf"
)


class _PromoteDisabled(unittest.TestCase):
    """build_fallback_data can reach the LinkedIn → capture-deep promote path;
    disable it so unit tests never make a network call."""

    def setUp(self):
        self._prev = os.environ.get("ATHENA_DISABLE_PLAYWRIGHT_PROMOTE")
        os.environ["ATHENA_DISABLE_PLAYWRIGHT_PROMOTE"] = "1"

    def tearDown(self):
        if self._prev is None:
            os.environ.pop("ATHENA_DISABLE_PLAYWRIGHT_PROMOTE", None)
        else:
            os.environ["ATHENA_DISABLE_PLAYWRIGHT_PROMOTE"] = self._prev


class TestConferenceTitleDerivation(_PromoteDisabled):
    """Issue #125 — conference prefix + acronym restoration + no 65-char clip."""

    def test_blackhat_pdf_full_derivation(self):
        result = apply_naming_convention(
            "Us 17 Robbins An Ace Up The Sleeve Designing Active Directory Dacl Backdoors",
            _BLACKHAT_URL,
            "paper",
        )
        # Structural assertions, NOT a full exact-string match: every token of
        # the output is config-derived (conference patterns, acronyms, separator,
        # length cap in athena.default.json), so a full-string pin would raise
        # false alarms on benign config edits. Assert the prefix + spine + tail
        # together; the acronym/tail invariants are pinned by their own tests.
        self.assertTrue(result.startswith("BlackHat USA 2017:"))
        self.assertIn("Robbins", result)
        self.assertIn("ACE", result)
        self.assertIn("DACL", result)
        self.assertTrue(result.endswith("Backdoors"))

    def test_acronyms_restored_not_titlecased(self):
        result = apply_naming_convention(
            "Us 17 Robbins An Ace Up The Sleeve Designing Active Directory Dacl Backdoors",
            _BLACKHAT_URL, "paper",
        )
        self.assertIn("ACE", result)
        self.assertIn("DACL", result)
        self.assertNotIn(" Ace ", result)
        self.assertNotIn("Dacl", result)

    def test_tail_survives_old_65_char_cap(self):
        """The old hardcoded 65-char filename cap clipped '...Backdoors'."""
        result = apply_naming_convention(
            "Us 17 Robbins An Ace Up The Sleeve Designing Active Directory Dacl Backdoors",
            _BLACKHAT_URL, "paper",
        )
        self.assertTrue(result.endswith("Backdoors"))
        self.assertGreater(len(result), 65)


class TestGenericH1FrontmatterLineSkip(_PromoteDisabled):
    """Issue #116 — a `source:` frontmatter-pair line must not become the title
    when the H1 is a filtered generic title."""

    def test_source_line_not_promoted_to_title(self):
        body = (
            "source: https://x.com/jdoe/status/123\n"
            "# Tweet\n\n"
            "This is the actual tweet body content that should win."
        )
        data = build_fallback_data(
            body, "https://x.com/jdoe/status/123", "webpage", hint_title="Tweet"
        )
        title = data["title"]
        self.assertEqual(title, "X: This is the actual tweet body content that should win.")
        self.assertNotIn("source:", title)
        self.assertNotIn("https://", title)


if __name__ == "__main__":
    unittest.main()
