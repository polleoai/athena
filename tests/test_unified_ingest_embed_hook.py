"""unified_ingest fires embed discovery for webpage ingests.

Anchor (2026-06-27): a webpage may embed its real content via <iframe> (slide
decks). ingest() must run embed_discovery for webpage results so those embeds get
queued as their own sources — and must NOT run it for non-webpage types. The
handler + discovery are stubbed so the test is offline.

Run from vault root:
    python3 -m pytest tests/test_unified_ingest_embed_hook.py -v
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

import unified_ingest as ui  # type: ignore  # noqa: E402
import embed_discovery as ed  # type: ignore  # noqa: E402


class EmbedHook(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kb-embed-hook-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.calls = []
        self._orig_handlers = dict(ui._HANDLERS)
        self._orig_discover = ed.discover_and_queue
        ed.discover_and_queue = lambda vault, url, html=None: (
            self.calls.append(url) or ["https://embed.example/deck/"]
        )
        self.addCleanup(self._restore)

    def _restore(self):
        ui._HANDLERS.clear()
        ui._HANDLERS.update(self._orig_handlers)
        ed.discover_and_queue = self._orig_discover

    def _stub_handler(self, source_type):
        def handler(inp, routing, canonical_url):
            return ui.IngestResult(
                raw_path=self.tmp / "r.md", source_type=source_type,
                canonical_url=canonical_url, title="t",
                extracted_via="stub", was_re_routed=False,
            )
        ui._HANDLERS[source_type] = handler

    def test_webpage_triggers_embed_discovery(self):
        self._stub_handler("webpage")
        # Route every URL to webpage by stubbing route() via source_hint isn't
        # reliable; instead patch route to force webpage.
        orig_route = ui.route
        ui.route = lambda url, hint="": {"source_type": "webpage", "url": url,
                                         "was_re_routed": False}
        try:
            res = ui.ingest(ui.IngestInput(
                vault_root=self.tmp, url="https://ex.org/page", body="x"))
        finally:
            ui.route = orig_route
        self.assertEqual(len(self.calls), 1)
        self.assertIn("https://embed.example/deck/", res.related_urls_queued)

    def test_non_webpage_skips_embed_discovery(self):
        self._stub_handler("paper")
        orig_route = ui.route
        ui.route = lambda url, hint="": {"source_type": "paper", "url": url,
                                         "was_re_routed": False}
        try:
            ui.ingest(ui.IngestInput(
                vault_root=self.tmp, url="https://arxiv.org/abs/1", body="x"))
        finally:
            ui.route = orig_route
        self.assertEqual(self.calls, [])


if __name__ == "__main__":
    unittest.main()
