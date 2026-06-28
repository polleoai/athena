"""Lint backstop: embed-iframe sources recorded but never captured get re-queued.

embed_discovery records container→embed mappings in inbox/embed-sources.tsv and
queues the embeds. If url-new.txt is processed-and-cleared before an embed is
captured, the embed would be lost. This lint check re-surfaces it. Network-free:
reads only local state. Idempotent: an already-captured (url-resolved.tsv) or
already-queued (url-new.txt) embed is not re-queued.

Run from vault root:
    python3 -m pytest tests/test_lint_embed_requeue.py -v
"""
from __future__ import annotations

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

CONTAINER = "https://www.provos.org/p/talks-ai-zero-days-and-invariants"
DECK1 = "https://ironcurtain.dev/uofm-secrit/"
DECK2 = "https://ironcurtain.dev/csa-zerodays/"


class EmbedRequeueLint(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kb-embed-requeue-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        (self.tmp / "bin").symlink_to(ROOT / "bin")
        for d in ("wiki/format/webpages", "raw/webpages/artifacts", "inbox"):
            (self.tmp / d).mkdir(parents=True, exist_ok=True)
        (self.tmp / "inbox" / "embed-sources.tsv").write_text(
            f"{CONTAINER}\t{DECK1}\n{CONTAINER}\t{DECK2}\n", encoding="utf-8"
        )

    def _lint(self):
        cmd_lint.handle([], str(self.tmp))

    def _url_new(self):
        f = self.tmp / "inbox" / "url-new.txt"
        if not f.exists():
            return set()
        return {ln.strip() for ln in f.read_text().splitlines() if ln.strip()}

    def test_uncaptured_embeds_are_requeued(self):
        self._lint()
        self.assertEqual(self._url_new(), {DECK1, DECK2})

    def test_captured_embed_not_requeued(self):
        (self.tmp / "inbox" / "url-resolved.tsv").write_text(
            f"captured\tDeck1\t{DECK1}\t2026-06-27\n", encoding="utf-8"
        )
        self._lint()
        self.assertEqual(self._url_new(), {DECK2})

    def test_idempotent(self):
        self._lint()
        self._lint()
        # Each embed appears exactly once in url-new.txt
        lines = [
            ln.strip()
            for ln in (self.tmp / "inbox" / "url-new.txt").read_text().splitlines()
            if ln.strip()
        ]
        self.assertEqual(sorted(lines), sorted([DECK1, DECK2]))


if __name__ == "__main__":
    unittest.main()
