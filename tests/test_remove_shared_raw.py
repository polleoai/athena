"""`kb remove --with-raw` / `kb delete` must not trash a raw another page uses.

Witnessed 2026-11-22 (JD Pruett / cover-your-ais). `kb merge` folded a
follow-up page into the original post: the follow-up's raw moved onto the
merged page's `raw_paths:` and was marked `merged_into:`. The follow-up page
then came back, and removing it took that raw to trash too, leaving the
merged page listing a missing source. A raw is the source of every page
that lists it, so removing one page must not pull it from under the others.

Also: `kb remove` is documented (CLAUDE.md, the plugin, the MCP tool) as
moving the page and its raw together, but the MCP tool and the plugin kept
the raw unless asked, so a "delete" from the Obsidian chat left an orphan
raw that lint then rebuilt into the page.

Rules:
  1. --with-raw trashes a raw only no other wiki page lists (raw_path or
     raw_paths); a shared one is kept, and the output names the page.
  2. A raw only this page uses is still trashed.
  3. The MCP kb_remove trashes the raw unless with_raw is false.

Run from vault root:
    python3 -m pytest tests/test_remove_shared_raw.py -v
"""
from __future__ import annotations

import io
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LIB = ROOT / "bin" / "lib"
for p in (LIB, ROOT / "server"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import cmd_remove  # type: ignore  # noqa: E402


def _page(title, fm_raw):
    return (f'---\ntitle: "{title}"\nsource_type: "webpage"\n{fm_raw}'
            f'url: "https://example.com/{title}"\ndate_added: 2026-11-22\n'
            f'last_updated: 2026-11-22\nrelated:\nsummary: "s"\n---\nbody\n')


class RemoveKeepsSharedRaw(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kb-remove-shared-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        (self.tmp / "bin").symlink_to(ROOT / "bin")
        for d in ("wiki/format/webpages", "raw/webpages/artifacts", "inbox"):
            (self.tmp / d).mkdir(parents=True, exist_ok=True)
        self.shared = "raw/webpages/artifacts/shared.md"
        self.own = "raw/webpages/artifacts/own.md"
        for rel in (self.shared, self.own, "raw/webpages/artifacts/m.md"):
            (self.tmp / rel).write_text('---\ntitle: "x"\n---\n\nx\n', encoding="utf-8")
        w = self.tmp / "wiki/format/webpages"
        # The merged page lists the shared raw among its sources.
        (w / "Merged.md").write_text(_page("Merged", (
            'raw_paths:\n  - "raw/webpages/artifacts/m.md"\n'
            f'  - "{self.shared}"\n')), encoding="utf-8")
        # The duplicate lists the shared raw plus one only it uses.
        (w / "Dup.md").write_text(_page("Dup", (
            f'raw_paths:\n  - "{self.shared}"\n  - "{self.own}"\n')), encoding="utf-8")

    def remove(self, *args):
        out = io.StringIO()
        with redirect_stdout(out):
            rc = cmd_remove.handle(list(args), str(self.tmp))
        return rc, out.getvalue()

    def test_shared_raw_kept_and_named(self):
        rc, out = self.remove("Dup", "--with-raw", "--yes")
        self.assertEqual(rc or 0, 0, out)
        self.assertFalse((self.tmp / "wiki/format/webpages/Dup.md").exists(), "the page itself is removed")
        self.assertTrue((self.tmp / self.shared).exists(), "a raw the merged page lists is kept")
        self.assertIn("[[Merged]]", out, "the output says which page still uses it")

    def test_own_raw_still_trashed(self):
        self.remove("Dup", "--with-raw", "--yes")
        self.assertFalse((self.tmp / self.own).exists(), "a raw only this page used goes with it")

    def test_single_raw_path_counts_as_a_user(self):
        w = self.tmp / "wiki/format/webpages"
        (w / "Merged.md").write_text(_page("Merged", f'raw_path: "{self.shared}"\n'), encoding="utf-8")
        self.remove("Dup", "--with-raw", "--yes")
        self.assertTrue((self.tmp / self.shared).exists(), "raw_path: lists a user too")


class McpRemoveDefaultsToWithRaw(unittest.TestCase):
    def setUp(self):
        try:
            from athena import server  # type: ignore
        except Exception as e:  # noqa: BLE001
            self.skipTest(f"MCP server not importable here: {e}")
        self.server = server

    def test_default_trashes_the_raw(self):
        self.assertEqual(self.server._remove_args({"page": "P"}), ["P", "--yes", "--with-raw"])

    def test_with_raw_false_keeps_it(self):
        self.assertEqual(self.server._remove_args({"page": "P", "with_raw": False}), ["P", "--yes"])


if __name__ == "__main__":
    unittest.main()
