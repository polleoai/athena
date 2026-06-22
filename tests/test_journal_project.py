"""Tests for `kb journal --project` / `kb reflect --project` (Feature 3).

Covers the multi-project journal dimension:
  - the no-project path stays byte-identical to the legacy journal (parity-safe),
  - --project writes under wiki/journal/<safe(project)>/ with `project:`
    frontmatter and the seeded retrospective template,
  - second same-day/project entry appends without re-seeding,
  - odd project names sanitize into a safe subdir,
  - reflect scoping only sees the requested project's subdir.

Run from vault root:  python3 -m pytest tests/test_journal_project.py -v
"""
from __future__ import annotations

import sys
import tempfile
import time
import unittest
from pathlib import Path

LIB = Path(__file__).resolve().parent.parent / "bin" / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

import wiki_page  # noqa: E402
import reflect  # noqa: E402
from wiki_writer import _safe_filename  # noqa: E402


class JournalProject(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.vault = self._tmp.name
        (Path(self.vault) / "wiki" / "journal").mkdir(parents=True, exist_ok=True)
        self.today = time.strftime("%Y-%m-%d")

    def tearDown(self):
        self._tmp.cleanup()

    # ── no-project path: unchanged behavior (parity-safe) ──────────
    def test_no_project_path_and_format(self):
        result = wiki_page.create_journal_entry(self.vault, "hello")
        self.assertEqual(result["status"], "saved")
        self.assertEqual(result["date"], self.today)
        self.assertIsNone(result["project"])
        self.assertEqual(result["file_path"], f"wiki/journal/Journal-{self.today}.md")

        path = Path(self.vault) / "wiki" / "journal" / f"Journal-{self.today}.md"
        self.assertTrue(path.exists())
        content = path.read_text(encoding="utf-8")
        # Same frontmatter as the legacy journal: no `project:` key.
        self.assertNotIn("project:", content)
        # No retrospective template sections on the top-level file.
        for section in ("## Done", "## Data", "## Problems", "## Learnings",
                        "## Tomorrow", "## KB-worthy"):
            self.assertNotIn(section, content)
        # Frontmatter shape verbatim.
        self.assertTrue(content.startswith(
            f'---\ntitle: "Journal-{self.today}"\nsource_type: "topic"\n'
            f"date_added: {self.today}\nlast_updated: {self.today}\n"
            f"tags: [journal]\nrelated:\n---\n\n"))
        # The note appears under a timestamped entry.
        self.assertRegex(content, r"### \d{2}:\d{2}\n\nhello\n")

    # ── with-project path: subdir + frontmatter + template ─────────
    def test_with_project(self):
        result = wiki_page.create_journal_entry(self.vault, "hello", project="X Growth")
        self.assertEqual(result["status"], "saved")
        self.assertEqual(result["project"], "X Growth")
        self.assertEqual(
            result["file_path"],
            f"wiki/journal/X Growth/Journal-{self.today}.md")

        path = Path(self.vault) / "wiki" / "journal" / "X Growth" / f"Journal-{self.today}.md"
        self.assertTrue(path.exists())
        content = path.read_text(encoding="utf-8")

        # project: frontmatter line present.
        self.assertIn('project: "X Growth"', content)
        # All 6 retrospective template sections present.
        for section in ("## Done", "## Data", "## Problems", "## Learnings",
                        "## Tomorrow", "## KB-worthy"):
            self.assertIn(section, content)
        # Note appears under a timestamp, AFTER the template sections.
        self.assertRegex(content, r"### \d{2}:\d{2}\n\nhello\n")
        self.assertLess(content.index("## KB-worthy"), content.index("hello"))

    # ── second same-day/project entry appends, no duplicate ────────
    def test_second_entry_same_day_appends(self):
        wiki_page.create_journal_entry(self.vault, "first", project="X Growth")
        wiki_page.create_journal_entry(self.vault, "second", project="X Growth")

        path = Path(self.vault) / "wiki" / "journal" / "X Growth" / f"Journal-{self.today}.md"
        content = path.read_text(encoding="utf-8")

        # Frontmatter written exactly once.
        self.assertEqual(content.count("source_type:"), 1)
        self.assertEqual(content.count('project: "X Growth"'), 1)
        # Template seeded exactly once.
        self.assertEqual(content.count("## Done"), 1)
        # Both notes present.
        self.assertIn("first", content)
        self.assertIn("second", content)

    # ── odd project name sanitizes into a safe subdir ──────────────
    def test_safe_filename_subdir(self):
        weird = 'Foo/Bar*Baz?"<>|'
        result = wiki_page.create_journal_entry(self.vault, "note", project=weird)
        safe = _safe_filename(weird)
        # The slash/odd chars must not create nested or reserved paths.
        self.assertNotIn("/", safe)
        self.assertNotIn("*", safe)
        self.assertNotIn("?", safe)
        expected = Path(self.vault) / "wiki" / "journal" / safe / f"Journal-{self.today}.md"
        self.assertTrue(expected.exists())
        # Original project name preserved verbatim in frontmatter.
        self.assertIn(f'project: "{weird}"', expected.read_text(encoding="utf-8"))

    # ── reflect scoping: project= only sees that subdir ────────────
    def test_reflect_scoping(self):
        # One entry in a project, one at top level, one in another project.
        wiki_page.create_journal_entry(self.vault, "growth note", project="X Growth")
        wiki_page.create_journal_entry(self.vault, "top level note")
        wiki_page.create_journal_entry(self.vault, "other note", project="Other Project")

        scoped = reflect.gather_journal_entries(self.vault, days=7, project="X Growth")
        texts = " ".join(e.get("text", "") for e in scoped)
        self.assertIn("growth note", texts)
        self.assertNotIn("top level note", texts)
        self.assertNotIn("other note", texts)

        # Unscoped gathering sees only the top-level entry (not project subdirs).
        unscoped = reflect.gather_journal_entries(self.vault, days=7)
        utexts = " ".join(e.get("text", "") for e in unscoped)
        self.assertIn("top level note", utexts)
        self.assertNotIn("growth note", utexts)

    # ── list_recent_journal walks project subdirs + annotates ──────
    def test_list_recent_includes_project(self):
        wiki_page.create_journal_entry(self.vault, "top", )
        wiki_page.create_journal_entry(self.vault, "proj note", project="X Growth")
        entries = wiki_page.list_recent_journal(self.vault)
        projects = {e.get("project") for e in entries}
        self.assertIn(None, projects)
        self.assertIn("X Growth", projects)


if __name__ == "__main__":
    unittest.main()
