"""A restored page's URL must not stay `removed` in inbox/url-resolved.tsv.

`kb remove` appends a `removed` row for the page's URL (Operating Principle
#4: the URL survives so the source can be recaptured). `kb undo` brought the
page back but left that row as the URL's latest status, so stats and the URL
Tracker reported a live page as removed. The plugin's own undo used to flip
it; once the plugin ran bin/kb (#180) the gap would have spread there too.

Fix at both ends (Principle #6/#7): undo appends a `captured` row for every
wiki page it restores, and lint auto-fixes any URL whose latest row is
`removed` while a wiki page with that URL exists.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "bin" / "lib"))
import snapshot  # noqa: E402
import url_status  # noqa: E402

PAGE = '---\ntitle: "Some Page"\nraw_path: "raw/webpages/some.md"\nurl: "{url}"\n---\nbody\n'


def _tsv(vault):
    p = Path(vault) / "inbox" / "url-resolved.tsv"
    return [l.split("\t") for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


class Revive(unittest.TestCase):
    def setUp(self):
        self.vault = tempfile.mkdtemp()
        for d in ("wiki/format/webpages", "inbox"):
            os.makedirs(os.path.join(self.vault, d))
        self.page = Path(self.vault, "wiki/format/webpages/Some Page.md")

    def write_tsv(self, *rows):
        Path(self.vault, "inbox/url-resolved.tsv").write_text(
            "".join("\t".join(r) + "\n" for r in rows), encoding="utf-8")

    def test_removed_row_with_a_live_page_is_revived(self):
        self.page.write_text(PAGE.format(url="https://e.com/a?utm_source=x"))
        self.write_tsv(["captured", "Some Page", "https://e.com/a", "2026-01-01T00:00:00Z"],
                       ["removed", "Some Page", "https://e.com/a", "https://e.com/a", ""])
        self.assertEqual(url_status.revive_removed(self.vault), ["Some Page"])
        last = _tsv(self.vault)[-1]
        self.assertEqual(last[:3], ["captured", "Some Page", "https://e.com/a?utm_source=x"])
        self.assertRegex(last[3], r"^\d{4}-\d{2}-\d{2}T")
        self.assertEqual(url_status.revive_removed(self.vault), [], "idempotent")

    def test_removed_without_a_page_is_left_alone(self):
        self.write_tsv(["removed", "Gone", "https://e.com/gone", "https://e.com/gone", ""])
        before = Path(self.vault, "inbox/url-resolved.tsv").read_bytes()
        self.assertEqual(url_status.revive_removed(self.vault), [])
        self.assertEqual(Path(self.vault, "inbox/url-resolved.tsv").read_bytes(), before)

    def test_only_restricts_to_the_given_pages(self):
        self.page.write_text(PAGE.format(url="https://e.com/a"))
        Path(self.vault, "wiki/format/webpages/Other.md").write_text(PAGE.format(url="https://e.com/b"))
        self.write_tsv(["removed", "Some Page", "https://e.com/a", "", ""],
                       ["removed", "Other", "https://e.com/b", "", ""])
        got = url_status.revive_removed(self.vault, only=["wiki/format/webpages/Other.md"])
        self.assertEqual(got, ["Other"])

    def test_no_tsv_is_a_no_op(self):
        self.page.write_text(PAGE.format(url="https://e.com/a"))
        self.assertEqual(url_status.revive_removed(self.vault), [])
        self.assertFalse(Path(self.vault, "inbox/url-resolved.tsv").exists())

    def test_undo_of_both_trash_shapes_revives(self):
        for shape in ("py", "js"):
            with self.subTest(shape=shape):
                self.setUp()
                self.page.write_text(PAGE.format(url="https://e.com/a"))
                self.write_tsv(["captured", "Some Page", "https://e.com/a", "2026-01-01T00:00:00Z"])
                if shape == "py":
                    snapshot.trash_files(self.vault, [str(self.page)], operation="remove", description="d")
                else:
                    name = time.strftime("%Y%m%dT%H%M%S", time.gmtime()) + "_kb-remove"
                    dest = Path(self.vault, ".kb-trash", name, "wiki/format/webpages/Some Page.md")
                    dest.parent.mkdir(parents=True)
                    shutil.move(str(self.page), dest)
                with Path(self.vault, "inbox/url-resolved.tsv").open("a") as fh:
                    fh.write("removed\tSome Page\thttps://e.com/a\thttps://e.com/a\t\n")
                ok, msg = snapshot.restore_last(self.vault)
                self.assertTrue(ok, msg)
                self.assertEqual(_tsv(self.vault)[-1][0], "captured")


class LintCheck(unittest.TestCase):
    def test_lint_auto_fixes_a_stale_removed_row(self):
        v = tempfile.mkdtemp(prefix="kb-urlstatus-")
        shutil.copytree(ROOT / "bin", os.path.join(v, "bin"))
        for d in ("wiki/format/webpages", "raw/webpages", "inbox"):
            os.makedirs(os.path.join(v, d))
        Path(v, "CLAUDE.md").write_text("# test vault\n")
        Path(v, "RULES.md").write_text("# RULES\n")
        Path(v, "wiki/format/webpages/Some Page.md").write_text(PAGE.format(url="https://e.com/a"))
        Path(v, "raw/webpages/some.md").write_text("raw")
        Path(v, "inbox/url-resolved.tsv").write_text(
            "removed\tSome Page\thttps://e.com/a\thttps://e.com/a\t\n", encoding="utf-8")
        r = subprocess.run([sys.executable, os.path.join(v, "bin", "kb"), "lint"], cwd=v,
                           capture_output=True, text=True)
        self.assertIn("removed in url-resolved.tsv but the page exists", r.stdout, r.stdout[-2000:])
        self.assertEqual(_tsv(v)[-1][0], "captured")


if __name__ == "__main__":
    unittest.main()
