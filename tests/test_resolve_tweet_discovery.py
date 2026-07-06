"""C1: resolve_tweet() must SURFACE a destination-tweet, not drop it.

When a tweet is a pointer whose t.co resolves to ANOTHER tweet's status URL
(the witnessed Atai→Saboo case), the old code hit a blanket `elif x.com ...:
continue` and discarded it ("No external link found"). C1 carves out that one
case: queue the destination (discover-and-surface) and record it in the
pointer tweet's own raw body — while still capturing the pointer tweet.

Network is fully mocked: _curl (oembed + HEAD) and _run (arcus) are stubbed,
and _queue_destination_tweet is captured so no inbox write happens.

Run from vault root:
    python3 -m pytest tests/test_resolve_tweet_discovery.py -v
"""
from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY_CAPTURE = ROOT / "bin" / "lib" / "capture.py"
LIB = ROOT / "bin" / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))


def _load_capture() -> types.ModuleType:
    src = PY_CAPTURE.read_text()
    mod = types.ModuleType("kb_capture_under_test_c1")
    mod.__file__ = str(PY_CAPTURE)
    exec(compile(src, str(PY_CAPTURE), "exec"), mod.__dict__)
    return mod


class _FakeProc:
    def __init__(self, returncode=1, stdout=""):
        self.returncode = returncode
        self.stdout = stdout


class ResolveTweetDiscovery(unittest.TestCase):
    def setUp(self):
        self.mod = _load_capture()
        self.queued: list[tuple[str, str]] = []
        # Stub the destination-queue so no inbox write happens.
        self.mod._queue_destination_tweet = lambda dest, src: self.queued.append((dest, src))
        # Stub arcus subprocess — return "no content" so the oembed text path runs.
        self.mod._run = lambda *a, **k: _FakeProc(returncode=1, stdout="")

    def _curl_factory(self, location: str):
        """Return a fake _curl: oembed → html with a t.co; HEAD → Location."""
        def fake_curl(args, timeout_extra=30):
            joined = " ".join(args)
            if "publish.twitter.com/oembed" in joined:
                return '{"html": "<blockquote>pointer https://t.co/r4idhmoRy2</blockquote>"}'
            if "-sI" in args:
                return f"HTTP/2 301\r\nlocation: {location}\r\n\r\n"
            return ""
        return fake_curl

    def _run_resolve(self, original_url: str, location: str):
        self.mod._curl = self._curl_factory(location)
        state = {"desc": "", "keywords": "", "category": "",
                 "discovered_via": "", "content_chars": None, "pdf_file": ""}
        ret = self.mod.resolve_tweet(original_url, original_url, state)
        return ret, state

    # ── the witnessed case ────────────────────────────────────────────────
    def test_destination_tweet_is_queued_and_recorded(self):
        original = "https://x.com/ataiiam/status/2062236697534812299"
        dest = "https://x.com/Saboo_Shubham_/status/2062220865643982875"
        ret, state = self._run_resolve(original, dest)
        # Queued for surfacing (discover-and-surface)
        self.assertEqual(len(self.queued), 1)
        self.assertEqual(self.queued[0][0], dest)
        # Pointer tweet is STILL captured (return original, not the destination)
        self.assertEqual(ret, original)
        # Destination recorded in the pointer's own raw body for C2/lint pickup
        self.assertIn(dest, state.get("tweet_text", ""))

    def test_destination_with_query_string_normalized(self):
        original = "https://x.com/ataiiam/status/2062236697534812299"
        ret, state = self._run_resolve(
            original, "https://twitter.com/Saboo_Shubham_/status/2062220865643982875?s=20")
        self.assertEqual(len(self.queued), 1)
        self.assertEqual(self.queued[0][0],
                         "https://x.com/Saboo_Shubham_/status/2062220865643982875")

    # ── guards: must NOT queue ────────────────────────────────────────────
    def test_self_reference_not_queued(self):
        original = "https://x.com/ataiiam/status/2062236697534812299"
        ret, state = self._run_resolve(original, original)  # resolves to itself
        self.assertEqual(self.queued, [])
        self.assertEqual(ret, original)

    def test_profile_link_not_queued(self):
        original = "https://x.com/ataiiam/status/2062236697534812299"
        ret, state = self._run_resolve(original, "https://x.com/someuser")
        self.assertEqual(self.queued, [])
        self.assertEqual(ret, original)

    def test_media_link_not_queued(self):
        original = "https://x.com/ataiiam/status/2062236697534812299"
        ret, state = self._run_resolve(original, "https://pic.twitter.com/abc123")
        self.assertEqual(self.queued, [])
        self.assertEqual(ret, original)

    # ── existing behavior preserved: external link still redirects ─────────
    def test_external_link_still_redirects(self):
        original = "https://x.com/ataiiam/status/2062236697534812299"
        ret, state = self._run_resolve(original, "https://example.com/the-real-article")
        self.assertEqual(ret, "https://example.com/the-real-article")
        self.assertEqual(state["discovered_via"], original)
        self.assertEqual(self.queued, [])


if __name__ == "__main__":
    unittest.main()
