"""Offline unit tests for bin/lib/fetch_tweet.py.

fetch_tweet.py reads X's public syndication CDN and emits a clean raw .md —
real author + full text + media for tweets, and the real title/preview/cover
for long-form X Articles (whose visible body is just a t.co pointer). These
tests exercise the pure builders against fixture JSON; no network is touched.

The motivating bug: x.com/FakeMaidenMaker/status/2064900447375085823 (an X
Article) captured via the plugin DOM walker landed with the t.co shortlink as
the title and a truncated body. The syndication path recovers the real title,
author, cover, and preview.

Run from vault root:
    python3 -m pytest tests/test_fetch_tweet.py -v
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LIB = ROOT / "bin" / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

import fetch_tweet as ft  # type: ignore  # noqa: E402


ARTICLE_FIXTURE = {
    "id_str": "2064900447375085823",
    "text": "https://t.co/akLfrSY9N5",
    "lang": "zxx",
    "user": {"name": "Ren", "screen_name": "FakeMaidenMaker"},
    "article": {
        "rest_id": "2064847409394446336",
        "title": "AI Monetization Beginner's Guide",
        "preview_text": "If you are a complete AI beginner, you probably have "
                        "two questions. This article answers them all.",
        "cover_media": {"media_info": {
            "original_img_url": "https://pbs.twimg.com/media/HKf0.jpg"}},
    },
}

TWEET_FIXTURE = {
    "id_str": "2043719893217128839",
    "text": "The fastest-growing open-source repo is a memory plugin. "
            "See https://t.co/abc",
    "user": {"name": "Aakash Gupta", "screen_name": "aakashgupta"},
    "entities": {
        "urls": [{"url": "https://t.co/abc",
                  "expanded_url": "https://github.com/acme/claude-mem",
                  "display_url": "github.com/acme/claude-mem"}],
        "media": [{"url": "https://t.co/imgshort"}],
    },
    "mediaDetails": [
        {"type": "photo", "media_url_https": "https://pbs.twimg.com/media/HFz.jpg"},
    ],
}

LONG_TWEET_FIXTURE = {
    "id_str": "1",
    "text": "short truncated form…",
    "note_tweet": {"text": "The full long-form note text that the 280-char "
                           "field truncates with an ellipsis."},
    "user": {"name": "Writer", "screen_name": "writer"},
}

QUOTE_FIXTURE = {
    "id_str": "2",
    "text": "Great point below",
    "user": {"name": "Quoter", "screen_name": "quoter"},
    "quoted_tweet": {
        "text": "Original insight here",
        "user": {"name": "Source", "screen_name": "source"},
    },
}


class ParseStatusUrl(unittest.TestCase):
    def test_x_com(self):
        self.assertEqual(
            ft.parse_status_url("https://x.com/Ren/status/2064900447375085823"),
            ("Ren", "2064900447375085823"))

    def test_twitter_com_with_query(self):
        self.assertEqual(
            ft.parse_status_url("https://twitter.com/a_b/status/123?s=20"),
            ("a_b", "123"))

    def test_non_status_url_is_none(self):
        self.assertIsNone(ft.parse_status_url("https://x.com/Ren"))
        self.assertIsNone(ft.parse_status_url("https://github.com/a/b"))


class ArticleRaw(unittest.TestCase):
    def setUp(self):
        self.url = "https://x.com/FakeMaidenMaker/status/2064900447375085823"
        self.raw = ft.build_raw(self.url, ARTICLE_FIXTURE)

    def test_title_is_article_title_not_tco(self):
        self.assertIn('title: "AI Monetization Beginner\'s Guide"', self.raw)
        self.assertNotIn("t.co", self.raw.split("---", 2)[1])  # not in frontmatter

    def test_clipped_via_is_syndication(self):
        self.assertIn('clipped_via: "tweet-syndication"', self.raw)

    def test_author_and_source_present(self):
        self.assertIn('author: "Ren @FakeMaidenMaker"', self.raw)
        self.assertIn(f'source: "{self.url}"', self.raw)

    def test_cover_preview_and_article_link(self):
        self.assertIn("![Cover](https://pbs.twimg.com/media/HKf0.jpg)", self.raw)
        self.assertIn("answers them all", self.raw)
        self.assertIn("https://x.com/i/article/2064847409394446336", self.raw)


class TweetRaw(unittest.TestCase):
    def test_expands_tco_and_drops_media_tco(self):
        raw = ft.build_raw("https://x.com/aakashgupta/status/2043719893217128839",
                           TWEET_FIXTURE)
        self.assertIn("https://github.com/acme/claude-mem", raw)
        self.assertNotIn("t.co/abc", raw)
        self.assertNotIn("t.co/imgshort", raw)

    def test_includes_media_image(self):
        raw = ft.build_raw("https://x.com/aakashgupta/status/2043719893217128839",
                           TWEET_FIXTURE)
        self.assertIn("![Image](https://pbs.twimg.com/media/HFz.jpg)", raw)

    def test_prefers_note_tweet_for_long_tweets(self):
        raw = ft.build_raw("https://x.com/writer/status/1", LONG_TWEET_FIXTURE)
        self.assertIn("The full long-form note text", raw)
        self.assertNotIn("truncated form", raw)

    def test_quote_tweet_rendered_as_blockquote(self):
        raw = ft.build_raw("https://x.com/quoter/status/2", QUOTE_FIXTURE)
        self.assertIn("Quoting Source @source", raw)
        self.assertIn("> Original insight here", raw)


class BuildBodyForCli(unittest.TestCase):
    """The CLI consumes build_body() (no frontmatter) and adds its own."""

    def test_body_has_no_frontmatter(self):
        body = ft.build_body("https://x.com/writer/status/1", LONG_TWEET_FIXTURE)
        self.assertFalse(body.lstrip().startswith("---"))
        self.assertIn("The full long-form note text", body)

    def test_article_title_helper(self):
        self.assertEqual(ft.article_title(ARTICLE_FIXTURE),
                         "AI Monetization Beginner's Guide")
        self.assertEqual(ft.article_title(TWEET_FIXTURE), "")


class SecurityHardening(unittest.TestCase):
    """Regression for code-review findings (2026-06-15)."""

    def test_yaml_escape_strips_newlines(self):
        # A newline in untrusted content must not break out of the quoted
        # frontmatter scalar (it would inject arbitrary keys).
        out = ft._yaml_escape('title\ninjected: true\nx')
        self.assertNotIn("\n", out)
        self.assertNotIn("\r", out)

    def test_yaml_escape_quotes_and_backslash(self):
        self.assertEqual(ft._yaml_escape('a"b\\c'), 'a\\"b\\\\c')

    def test_parse_status_url_host_anchored(self):
        # A path segment merely containing x.com must NOT be read as a tweet.
        self.assertIsNone(ft.parse_status_url("https://evil.com/x.com/u/status/123"))
        self.assertEqual(ft.parse_status_url("https://x.com/u/status/123"), ("u", "123"))
        self.assertEqual(
            ft.parse_status_url("https://mobile.twitter.com/u/status/9"), ("u", "9"))


class ArticleUrlHelpers(unittest.TestCase):
    def test_is_x_article_true_for_article(self):
        self.assertTrue(ft.is_x_article(ARTICLE_FIXTURE))

    def test_is_x_article_false_for_plain_tweet(self):
        self.assertFalse(ft.is_x_article(TWEET_FIXTURE))

    def test_article_url_from_rest_id(self):
        self.assertEqual(
            ft.article_url(ARTICLE_FIXTURE),
            "https://x.com/i/article/" + str(
                (ARTICLE_FIXTURE["article"].get("rest_id")
                 or ARTICLE_FIXTURE["article"].get("id"))))

    def test_article_url_none_for_plain_tweet(self):
        self.assertIsNone(ft.article_url(TWEET_FIXTURE))


if __name__ == "__main__":
    unittest.main()
