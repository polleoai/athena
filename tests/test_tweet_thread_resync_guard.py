"""Option A richness guard: a Web-Clipper author self-thread index must NOT be
re-captured via syndication (which returns only the head tweet, dropping the
thread). Single-tweet clips must still resync as before.

Witnessed: gengdaj/2069425112651272493 — a 7-section, ~30-link Codex thread was
stored as a 22-line syndication stub. See process_clip._clip_is_self_thread."""
import os
import sys
import unittest

LIB = os.path.join(os.path.dirname(__file__), "..", "bin", "lib")
if LIB not in sys.path:
    sys.path.insert(0, LIB)

import process_clip as pc  # noqa: E402

THREAD_URL = "https://x.com/gengdaJ/status/2069425112651272493"

# A thread index: the head tweet links to several of @gengdaJ's OWN tweets.
THREAD_BODY = """\
最近这几个月分享了太多关于Codex的玩法了。
一、边玩边赚钱
1. https://x.com/gengdaJ/status/2053724702993190917
2. https://x.com/gengdaJ/status/2052569175755829477
二、记忆系统
1. https://x.com/gengdaJ/status/2067985719675773192
2. https://x.com/gengdaJ/status/2068555151733043504
"""

# A plain single tweet — no sibling self-links.
SINGLE_BODY = "Just shipped a thing. No thread here."

# A scraped timeline fragment: many /status/ links, but OTHER authors' tweets.
TIMELINE_GARBAGE = """\
Home timeline.
https://x.com/someoneelse/status/111
https://x.com/another/status/222
https://x.com/third/status/333
https://x.com/fourth/status/444
"""

SOCIAL_FM = {"clipped_via": "web-clipper-social"}


class SelfThreadDetection(unittest.TestCase):
    def test_thread_body_is_self_thread(self):
        self.assertTrue(pc._clip_is_self_thread(THREAD_URL, THREAD_BODY))

    def test_single_tweet_is_not_self_thread(self):
        self.assertFalse(pc._clip_is_self_thread(THREAD_URL, SINGLE_BODY))

    def test_timeline_garbage_is_not_self_thread(self):
        # Links belong to other authors, not @gengdaJ → not a self-thread.
        self.assertFalse(pc._clip_is_self_thread(THREAD_URL, TIMELINE_GARBAGE))

    def test_head_self_link_does_not_count(self):
        # A tweet that only links to itself is not a thread.
        body = f"See {THREAD_URL}"
        self.assertFalse(pc._clip_is_self_thread(THREAD_URL, body))

    def test_non_tweet_url(self):
        self.assertFalse(pc._clip_is_self_thread("https://example.com/x", THREAD_BODY))


class ShouldResyncGuard(unittest.TestCase):
    def setUp(self):
        # Ensure the env kill-switch isn't set by the surrounding suite.
        self._saved = os.environ.pop("ATHENA_DISABLE_TWEET_RESYNC", None)

    def tearDown(self):
        if self._saved is not None:
            os.environ["ATHENA_DISABLE_TWEET_RESYNC"] = self._saved

    def test_thread_clip_is_not_resynced(self):
        self.assertFalse(pc._should_resync_tweet(THREAD_URL, SOCIAL_FM, THREAD_BODY))

    def test_single_tweet_clip_is_resynced(self):
        self.assertTrue(pc._should_resync_tweet(THREAD_URL, SOCIAL_FM, SINGLE_BODY))

    def test_empty_body_defaults_to_resync(self):
        # Back-compat: no body provided → original single-tweet behavior.
        self.assertTrue(pc._should_resync_tweet(THREAD_URL, SOCIAL_FM))

    def test_env_killswitch_disables_resync(self):
        os.environ["ATHENA_DISABLE_TWEET_RESYNC"] = "1"
        self.assertFalse(pc._should_resync_tweet(THREAD_URL, SOCIAL_FM, SINGLE_BODY))

    def test_non_clipper_source_not_resynced(self):
        self.assertFalse(
            pc._should_resync_tweet(THREAD_URL, {"clipped_via": "tweet-syndication"}, SINGLE_BODY))


class SocialThreadDecode(unittest.TestCase):
    # A web-clipper-social escaped-array capture: markdown-escaped brackets,
    # literal \n / \" escapes, relative ("/handle/status/id") link hrefs.
    ESCAPED = (
        '\\["\n\n'
        'Thread opener line one.\\n\\n'
        'I. Section\\n'
        '1. First item '
        '[https://x.com/gengdaJ/status/2053724702993190917…]'
        '("/gengdaJ/status/2053724702993190917")\\n'
        '2. Second item '
        '[https://x.com/gengdaJ/status/2052569175755829477…]'
        '("/gengdaJ/status/2052569175755829477")'
        '\n\n","\n\n'
        'Quoted tweet body with an \\"escaped quote\\" inside.'
        '\n\n"\\]'
    )

    def test_decodes_to_clean_markdown(self):
        out = pc._decode_social_thread(self.ESCAPED)
        self.assertIsNotNone(out)
        # Real newlines, not literal escapes.
        self.assertNotIn("\\n", out)
        # Links become clean absolute bare URLs; no broken relative hrefs.
        self.assertNotIn('](', out)
        self.assertIn("https://x.com/gengdaJ/status/2053724702993190917", out)
        self.assertIn("https://x.com/gengdaJ/status/2052569175755829477", out)
        # Tweets separated by a horizontal rule.
        self.assertIn("\n---\n", out)
        # Escaped quotes in the quoted tweet are unescaped, no stray backslashes.
        self.assertIn('"escaped quote"', out)
        self.assertNotIn("\\", out)

    def test_clean_body_passes_through(self):
        # A normal markdown body is not in the escaped-array shape → untouched.
        self.assertIsNone(pc._decode_social_thread("Just a normal tweet body."))

    def test_decoded_body_is_detected_as_self_thread(self):
        out = pc._decode_social_thread(self.ESCAPED)
        # Only 2 sibling links here (below the >=3 threshold) — so NOT a thread,
        # but the link extraction must still see them as @gengdaJ's own tweets.
        ids = pc._self_thread_status_ids(out, "gengdaJ")
        self.assertEqual(len(ids), 2)


if __name__ == "__main__":
    unittest.main()
