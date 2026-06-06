"""Frontmatter must stay on a single physical line per scalar.

A double-quoted YAML scalar that spans physical lines (e.g. an X.com <title>
that packs the whole multi-line tweet into the title) is technically valid
YAML via line folding, but Obsidian's Properties parser rejects it as
"Invalid properties". raw_writer._yaml_escape must escape newlines so any
value renders on one line. Witnessed 2026-05-26 (elvis/omarsar0 X post).
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

_VAULT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_VAULT / "bin" / "lib"))

from raw_writer import _yaml_escape, _format_fm_value  # noqa: E402


class TestYamlEscapeSingleLine(unittest.TestCase):
    def test_newlines_become_escape_sequences(self):
        s = 'elvis on X: "New research\n\nI see a lot...\n\nhttps://t.co/x" / X'
        out = _yaml_escape(s)
        self.assertNotIn("\n", out)              # no literal newline survives
        self.assertIn("\\n", out)                # rendered as an escape instead
        self.assertIn('\\"', out)                # embedded quotes still escaped

    def test_rendered_frontmatter_line_is_single_physical_line(self):
        s = "line one\nline two\nline three"
        line = _format_fm_value("title", s)
        self.assertEqual(line.count("\n"), 0)    # one physical line
        self.assertTrue(line.startswith('title: "'))

    def test_plain_value_unchanged(self):
        self.assertEqual(_yaml_escape("Plain Title"), "Plain Title")

    def test_tab_and_cr_escaped(self):
        self.assertEqual(_yaml_escape("a\tb\rc"), "a\\tb\\rc")


class TestMultilineScalarCollapse(unittest.TestCase):
    """Mirrors kb lint #46b: collapse a multi-physical-line quoted scalar in a
    frontmatter block onto one line (the auto-fix for files written before the
    _yaml_escape fix)."""

    @staticmethod
    def _collapse(fm_text: str) -> str:
        def _repl(mm: re.Match) -> str:
            return mm.group(1) + mm.group(2).replace("\r", "").replace("\n", "\\n") + mm.group(3)
        return re.sub(r'(: ")((?:[^"\\\n]|\\.|\n)*?)(")', _repl, fm_text)

    def test_collapses_multiline_title_only(self):
        fm = (
            'title: "elvis on X: \\"New research\n\nI see a lot...\\" / X"\n'
            'source: "https://x.com/omarsar0/status/123"\n'
            'captured_at: "2026-05-27T01:21:05Z"\n'
        )
        out = self._collapse(fm)
        # title is now one physical line; the other keys are unchanged.
        self.assertEqual(out.count("\n"), 3)  # three key lines, trailing \n
        self.assertIn("\\n\\nI see a lot", out)
        self.assertIn('source: "https://x.com/omarsar0/status/123"', out)
        import yaml
        self.assertTrue(yaml.safe_load(out))  # still valid YAML

    def test_wellformed_frontmatter_untouched(self):
        fm = 'title: "Clean Title"\nsource: "https://example.com"\n'
        self.assertEqual(self._collapse(fm), fm)


if __name__ == "__main__":
    unittest.main()
