"""`kb delete` takes a raw's asset dir with it, and `kb undo` brings it back.

raw/assets/<raw stem>/ holds a raw's local images: a webpage's downloaded
images, and since arcus 0.8.0 a paper's PDF figures. `kb remove --with-raw`
(= `kb delete`) moved the page, the raw and its PDF to trash but left the
asset dir behind, so every delete left an orphan that lint then listed, and
`kb undo` could not give the images back. Found 2026-11-22 before
re-capturing every paper (each re-capture is a delete then an add).

Rules:
  1. A trashed raw's raw/assets/<stem>/ goes into the SAME trash batch.
  2. `kb undo` restores it; if a dir of that name was created since (a
     re-capture), the restored one replaces it, never nests inside it.
  3. A raw kept (plain `kb remove`, or still used by another page) keeps its
     asset dir.

Run from vault root:
    python3 -m pytest tests/test_remove_assets.py -v
"""
from __future__ import annotations

import io
import json
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LIB = ROOT / "bin" / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

import cmd_remove  # type: ignore  # noqa: E402
import snapshot  # type: ignore  # noqa: E402


def _page(title, fm_raw):
    return (f'---\ntitle: "{title}"\nsource_type: "paper"\n{fm_raw}'
            f'url: "https://example.org/{title}"\ndate_added: 2026-11-22\n'
            f'last_updated: 2026-11-22\nrelated:\nsummary: "s"\n---\nbody\n')


class DeleteTakesAssets(unittest.TestCase):
    def setUp(self):
        self.kb = Path(tempfile.mkdtemp(prefix="kb-remove-assets-"))
        self.addCleanup(shutil.rmtree, self.kb, ignore_errors=True)
        (self.kb / "bin").symlink_to(ROOT / "bin")
        for d in ("wiki/format/papers", "raw/papers/artifacts", "inbox"):
            (self.kb / d).mkdir(parents=True, exist_ok=True)
        self.raw = "raw/papers/artifacts/paper-a.md"
        (self.kb / self.raw).write_text('---\ntitle: "A"\n---\n\n![F1](../../assets/paper-a/figure-01-p1.png)\n')
        (self.kb / "raw/papers/artifacts/paper-a.pdf").write_bytes(b"%PDF-1.4\n")
        self.assets = self.kb / "raw/assets/paper-a"
        self.assets.mkdir(parents=True)
        (self.assets / "figure-01-p1.png").write_bytes(b"\x89PNG old")
        (self.assets / "_assets.json").write_text('{"page_slug": "paper-a"}')
        (self.kb / "wiki/format/papers/A.md").write_text(_page("A", f'raw_path: "{self.raw}"\n'))

    def remove(self, *args):
        out = io.StringIO()
        with redirect_stdout(out):
            rc = cmd_remove.handle(list(args), str(self.kb))
        return rc, out.getvalue()

    def last_batch(self) -> Path:
        batches = sorted(p for p in (self.kb / ".kb-trash").iterdir() if (p / "manifest.json").exists())
        return batches[-1]

    def test_delete_moves_the_asset_dir_into_the_same_batch(self):
        rc, out = self.remove("A", "--with-raw", "--yes")
        self.assertEqual(rc or 0, 0, out)
        self.assertFalse(self.assets.exists(), "no orphan asset dir left behind")
        batch = self.last_batch()
        manifest = json.loads((batch / "manifest.json").read_text())
        self.assertIn("raw/assets/paper-a", manifest["deleted"])
        self.assertIn(self.raw, manifest["deleted"])
        self.assertTrue((batch / "deleted/raw/assets/paper-a/figure-01-p1.png").exists())

    def test_undo_restores_it(self):
        self.remove("A", "--with-raw", "--yes")
        ok, msg = snapshot.restore_last(str(self.kb))
        self.assertTrue(ok, msg)
        self.assertEqual((self.assets / "figure-01-p1.png").read_bytes(), b"\x89PNG old")
        self.assertTrue((self.kb / self.raw).exists())

    def test_undo_replaces_a_dir_recreated_since_never_nests(self):
        self.remove("A", "--with-raw", "--yes")
        self.assets.mkdir(parents=True)  # a re-capture made new figures
        (self.assets / "figure-01-p1.png").write_bytes(b"\x89PNG new")
        ok, msg = snapshot.restore_last(str(self.kb))
        self.assertTrue(ok, msg)
        self.assertFalse((self.assets / "paper-a").exists(), "not nested inside the new dir")
        self.assertEqual((self.assets / "figure-01-p1.png").read_bytes(), b"\x89PNG old")

    def test_plain_remove_keeps_raw_and_assets(self):
        self.remove("A", "--yes")
        self.assertTrue((self.kb / self.raw).exists())
        self.assertTrue(self.assets.exists())

    def test_shared_raw_keeps_its_assets(self):
        (self.kb / "wiki/format/papers/B.md").write_text(_page("B", f'raw_path: "{self.raw}"\n'))
        self.remove("A", "--with-raw", "--yes")
        self.assertTrue((self.kb / self.raw).exists(), "B still uses the raw")
        self.assertTrue(self.assets.exists(), "so its images stay too")


if __name__ == "__main__":
    unittest.main()
