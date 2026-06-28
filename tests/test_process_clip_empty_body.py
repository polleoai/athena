"""process_clip: an empty-body clip falls back to network capture, not a hard fail.

Anchor (2026-06-27): the provos.org "Two Talks" page is mostly embedded slide-deck
<iframe>s, so the Web Clipper produced a frontmatter-only clip with an empty body.
process_clip used to `raise "clip body is empty"`, leaving the clip in the watched
inbox — the watcher retried it every cycle and spammed the error toast. The fix:
empty body + valid URL → fetch from the URL (like `kb add`); if that also fails,
quarantine the clip so the retry loop stops.

Network is stubbed via unified_ingest.ingest so the test is offline.

Run from vault root:
    python3 -m pytest tests/test_process_clip_empty_body.py -v
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LIB = ROOT / "bin" / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

import process_clip as pc  # type: ignore  # noqa: E402
import unified_ingest as ui  # type: ignore  # noqa: E402

EMPTY_CLIP = (
    "---\n"
    "title:\n"
    'source: "https://www.provos.org/p/talks-ai-zero-days-and-invariants/"\n'
    'clipped_via: "web-clipper-social"\n'
    "author:\n"
    "clipped_at: 2026-06-26T18:50:18-07:00\n"
    "---\n\n"
)


class EmptyBodyFallback(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kb-empty-clip-"))
        (self.tmp / "inbox" / "Clippings").mkdir(parents=True)
        (self.tmp / "clippings").mkdir(parents=True)
        self.clip = self.tmp / "inbox" / "Clippings" / "Two Talks.md"
        self.clip.write_text(EMPTY_CLIP, encoding="utf-8")
        self._orig_ingest = ui.ingest
        self._orig_touch = pc._touch_wiki_last_updated_for_url
        pc._touch_wiki_last_updated_for_url = lambda *a, **k: None
        self.addCleanup(self._restore)

    def _restore(self):
        ui.ingest = self._orig_ingest
        pc._touch_wiki_last_updated_for_url = self._orig_touch
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_empty_body_triggers_network_fetch(self):
        captured = {}
        fake_raw = self.tmp / "raw/webpages/artifacts/provos.md"
        fake_raw.parent.mkdir(parents=True, exist_ok=True)
        fake_raw.write_text("ok", encoding="utf-8")

        def fake_ingest(inp):
            captured["fetch_if_missing"] = inp.fetch_if_missing
            captured["url"] = inp.url
            return ui.IngestResult(
                raw_path=fake_raw, source_type="webpage", canonical_url=inp.url,
                title="Two Talks", extracted_via="arcus", was_re_routed=False,
            )

        ui.ingest = fake_ingest
        out = pc.process_clip(self.clip, self.tmp)
        self.assertEqual(out, fake_raw)
        # The fallback must fetch from the URL, not reuse the empty body.
        self.assertTrue(captured["fetch_if_missing"])
        self.assertIn("provos.org", captured["url"])

    def test_failed_fetch_quarantines_clip(self):
        def boom(inp):
            raise ui.UnifiedIngestError("arcus returned empty content")

        ui.ingest = boom
        with self.assertRaises(pc.ProcessClipError):
            pc.process_clip(self.clip, self.tmp)
        # Clip moved out of the watched inbox so the watcher stops retrying.
        self.assertFalse(self.clip.exists())
        self.assertTrue((self.tmp / "clippings" / "_failed" / "Two Talks.md").exists())


if __name__ == "__main__":
    unittest.main()
