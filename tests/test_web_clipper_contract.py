"""Contract the Athena Web Clipper templates must satisfy — a clip with
title/source/clipped_via ingests cleanly and provenance is preserved.

The shipped Web Clipper templates (assets/web-clipper/*.json) emit this exact
frontmatter shape; this test is the regression guard that fails if the ingest
contract those templates depend on drifts. See
tests/test_web_clipper_templates.py for the template-side validation.

Run from vault root:  python3 -m pytest tests/test_web_clipper_contract.py -v
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

_VAULT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_VAULT / "bin" / "lib"))

import process_clip  # noqa: E402
from raw_parser import read_raw_frontmatter  # noqa: E402


def _clip(title: str, source: str, clipped_via: str, body: str) -> str:
    return (
        "---\n"
        f'title: "{title}"\n'
        f'source: "{source}"\n'
        f'clipped_via: "{clipped_via}"\n'
        "---\n\n" + body + "\n"
    )


class WebClipperContract(unittest.TestCase):
    def setUp(self):
        # A thin/social clip must NOT try to auto-promote to capture-deep
        # (which would spawn Playwright) inside the offline test vault.
        self._prev = os.environ.get("ATHENA_DISABLE_PLAYWRIGHT_PROMOTE")
        os.environ["ATHENA_DISABLE_PLAYWRIGHT_PROMOTE"] = "1"
        # This class exercises the /status/ re-sync, so re-enable it (conftest
        # disables it suite-wide). The non-status tests here use non-/status/
        # URLs, so they never reach the network regardless.
        os.environ.pop("ATHENA_DISABLE_TWEET_RESYNC", None)
        self.tmp = Path(tempfile.mkdtemp(prefix="kb-clip-contract-"))
        self.addCleanup(self._restore_env)
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        (self.tmp / "bin").symlink_to(_VAULT / "bin")
        for d in ("inbox/Clippings", "raw/webpages/artifacts", "wiki/format/webpages"):
            (self.tmp / d).mkdir(parents=True, exist_ok=True)

    def _restore_env(self):
        if self._prev is None:
            os.environ.pop("ATHENA_DISABLE_PLAYWRIGHT_PROMOTE", None)
        else:
            os.environ["ATHENA_DISABLE_PLAYWRIGHT_PROMOTE"] = self._prev

    def _write_and_process(self, name: str, text: str) -> dict:
        clip = self.tmp / "inbox" / "Clippings" / name
        clip.write_text(text, encoding="utf-8")
        # process_clip(clip_path, vault_root) — clip first, vault second.
        raw_path = process_clip.process_clip(str(clip), str(self.tmp))
        fm, _ = read_raw_frontmatter(Path(raw_path))
        return fm

    def test_universal_clip_ingests_with_source(self):
        fm = self._write_and_process(
            "uni.md",
            _clip("Some Article", "https://example.com/a", "web-clipper",
                  "Body text here."),
        )
        src = (fm.get("source") or fm.get("url") or "").rstrip("/")
        self.assertEqual(src, "https://example.com/a")

    def test_social_clip_preserves_provenance(self):
        fm = self._write_and_process(
            "li.md",
            _clip("A LinkedIn post", "https://www.linkedin.com/posts/x_abc",
                  "web-clipper-social", "Post body content."),
        )
        self.assertEqual((fm.get("clipped_via") or "").strip(), "web-clipper-social")

    def _process(self, name: str, text: str):
        clip = self.tmp / "inbox" / "Clippings" / name
        clip.write_text(text, encoding="utf-8")
        raw_path = process_clip.process_clip(str(clip), str(self.tmp))
        return clip, Path(raw_path).read_text(encoding="utf-8")

    def test_x_status_clip_resyncs_via_syndication(self):
        # A web-clipped tweet's brittle DOM body is replaced by the clean
        # syndication capture, and the trigger clip is removed.
        import unittest.mock as mock
        import fetch_tweet
        tweet = {"id_str": "123", "text": "Real syndication tweet body.",
                 "user": {"name": "Foo", "screen_name": "foo"}}
        with mock.patch.object(fetch_tweet, "fetch_tweet_json", return_value=tweet):
            clip, raw = self._process(
                "tw.md", _clip("Foo tweet", "https://x.com/foo/status/123",
                               "web-clipper-social", "BRITTLE GARBAGE timeline mess"))
        self.assertIn("Real syndication tweet body", raw)
        self.assertNotIn("BRITTLE GARBAGE", raw)
        self.assertFalse(clip.exists(), "trigger clip should be removed after resync")

    def test_x_status_article_tweet_routes_capture_deep_to_article_url(self):
        import unittest.mock as mock
        import fetch_tweet
        article = {"id_str": "123", "text": "https://t.co/x",
                   "article": {"rest_id": "777", "title": "Long Article"},
                   "user": {"name": "Foo", "screen_name": "foo"}}
        seen = {}

        def fake_deep(url, vault):
            seen["url"] = url
            return None  # simulate no session → fall through to clip body

        with mock.patch.object(fetch_tweet, "fetch_tweet_json", return_value=article), \
                mock.patch.object(process_clip, "_run_capture_deep", side_effect=fake_deep):
            self._process("tw3.md", _clip("Foo", "https://x.com/foo/status/123",
                                          "web-clipper-social", "preview"))
        self.assertEqual(seen.get("url"), "https://x.com/i/article/777",
                         "article-tweet must route capture-deep to the article URL")

    def test_x_status_resync_falls_back_when_syndication_unavailable(self):
        import unittest.mock as mock
        import fetch_tweet
        with mock.patch.object(fetch_tweet, "fetch_tweet_json", return_value=None):
            _clip_p, raw = self._process(
                "tw2.md", _clip("Foo", "https://x.com/foo/status/999",
                                "web-clipper-social", "fallback body kept"))
        self.assertIn("fallback body kept", raw)

    def test_social_clips_localize_images_but_plain_pages_do_not(self):
        # web-clipper-social (X Article / LinkedIn) must run inline images
        # through asset_download so the local copy is self-contained; a plain
        # web-clipper page must NOT (it keeps the byte-identical ingest path).
        import unittest.mock as mock
        seen: list[str] = []

        def _spy(body, slug, vault):
            seen.append(slug)
            return body  # offline: pass through unchanged

        with mock.patch("asset_download.download_assets", side_effect=_spy):
            self._write_and_process(
                "soc.md",
                _clip("Soc", "https://x.com/i/article/123", "web-clipper-social",
                      "![](https://pbs.twimg.com/media/X.jpg)"))
            self._write_and_process(
                "plain.md",
                _clip("Plain", "https://example.com/p", "web-clipper",
                      "![](https://pbs.twimg.com/media/Y.jpg)"))
        self.assertEqual(len(seen), 1,
                         "localization must fire exactly once — for the social clip")


if __name__ == "__main__":
    unittest.main()
