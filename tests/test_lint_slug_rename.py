"""lint #39 renames a collision-bait raw slug without losing the raw or its images.

#39 renames a raw with a generic slug (post-linkedin.md, untitled.md, ...) to
its URL-derived slug and rewrites the wiki pages that point at it. Found
2026-11-22 while fixing its sibling (PGM asked for the asset dir to move
with the rename): the snapshot ran first and MOVED the raw into trash, so
the os.rename that followed failed ("rename failed") and the raw was simply
gone, with the wiki page still pointing at the old path.

Rules:
  1. The raw ends up at <url-derived slug>.md with its content, and the
     wiki pages that referenced it point at the new path.
  2. raw/assets/<old stem>/ moves to raw/assets/<new stem>/, and the raw's
     image links are rewritten to it.
  3. `kb undo` puts everything back: old raw, old assets, old references.

Run from vault root:
    python3 -m pytest tests/test_lint_slug_rename.py -v
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LIB = ROOT / "bin" / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

URL = "https://www.linkedin.com/posts/someone_agents-activity-7300000000000000000-abcd"
BODY = "\n\n" + "A LinkedIn post about agents with enough text to be a real raw. " * 12


class Rename(unittest.TestCase):
    def setUp(self):
        self.kb = Path(tempfile.mkdtemp(prefix="athena-lint39-"))
        self.addCleanup(shutil.rmtree, self.kb, ignore_errors=True)
        (self.kb / "CLAUDE.md").write_text("# vault")
        (self.kb / "RULES.md").write_text("# RULES\n")
        for d in ("wiki/format/webpages", "inbox", "raw/webpages/artifacts", "raw/assets/post-linkedin"):
            (self.kb / d).mkdir(parents=True, exist_ok=True)
        shutil.copytree(ROOT / "bin", self.kb / "bin", ignore=shutil.ignore_patterns("__pycache__"))
        self.old = self.kb / "raw/webpages/artifacts/post-linkedin.md"
        self.old.write_text(f'---\ntitle: "Agents"\nsource: "{URL}"\n---\n\n'
                            f'![chart](../../assets/post-linkedin/abc.png)' + BODY)
        (self.kb / "raw/assets/post-linkedin/abc.png").write_bytes(b"\x89PNG img")
        self.page = self.kb / "wiki/format/webpages/LinkedIn: Agents.md"
        self.page.write_text(
            '---\ntitle: "LinkedIn: Agents"\nsource_type: "webpage"\n'
            'raw_path: "raw/webpages/artifacts/post-linkedin.md"\n'
            f'url: "{URL}"\ndate_added: 2026-11-22\nlast_updated: 2026-11-22\n'
            'related:\nsummary: "s"\n---\n\n## LinkedIn: Agents\n\nbody\n')
        from wiki_schema import make_slug, _url_to_slug_input  # type: ignore
        self.new_stem = make_slug(_url_to_slug_input(URL))
        self.assertNotEqual(self.new_stem, "post-linkedin")

    def kb_cmd(self, *args, stdin=""):
        return subprocess.run([sys.executable, str(self.kb / "bin" / "kb"), *args], cwd=self.kb,
                              input=stdin, capture_output=True, text=True, timeout=120,
                              env=dict(os.environ, KB_ROOT=str(self.kb)))

    def test_raw_and_refs_follow_the_rename(self):
        out = self.kb_cmd("lint").stdout
        new = self.kb / f"raw/webpages/artifacts/{self.new_stem}.md"
        self.assertNotIn("rename failed", out)
        self.assertTrue(new.exists(), out[-1500:])
        self.assertFalse(self.old.exists())
        self.assertIn("A LinkedIn post about agents", new.read_text())
        self.assertIn(f"raw/webpages/artifacts/{self.new_stem}.md", self.page.read_text())

    def test_assets_follow_the_rename(self):
        self.kb_cmd("lint")
        new = self.kb / f"raw/webpages/artifacts/{self.new_stem}.md"
        self.assertTrue((self.kb / f"raw/assets/{self.new_stem}/abc.png").exists())
        self.assertFalse((self.kb / "raw/assets/post-linkedin").exists())
        self.assertIn(f"](../../assets/{self.new_stem}/abc.png)", new.read_text())

    def test_existing_target_asset_dir_skips_before_anything_moves(self):
        (self.kb / f"raw/assets/{self.new_stem}").mkdir(parents=True)
        out = self.kb_cmd("lint").stdout
        self.assertIn(f"target raw/assets/{self.new_stem}/ already exists", out)
        self.assertTrue(self.old.exists(), "the raw was not moved anywhere")
        self.assertTrue((self.kb / "raw/assets/post-linkedin/abc.png").exists())

    def test_undo_restores_all(self):
        self.kb_cmd("lint")
        r = self.kb_cmd("undo", stdin="y\n")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(self.old.exists(), r.stdout)
        self.assertIn("](../../assets/post-linkedin/abc.png)", self.old.read_text())
        self.assertTrue((self.kb / "raw/assets/post-linkedin/abc.png").exists())
        self.assertFalse((self.kb / f"raw/assets/{self.new_stem}").exists())
        self.assertFalse((self.kb / f"raw/webpages/artifacts/{self.new_stem}.md").exists())
        self.assertIn("raw/webpages/artifacts/post-linkedin.md", self.page.read_text())


if __name__ == "__main__":
    unittest.main()
