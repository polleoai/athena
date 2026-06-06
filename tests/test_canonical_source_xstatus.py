"""C2: extract_canonical_urls() must recognize x.com/twitter status URLs.

A link-share tweet pointing to ANOTHER tweet (the witnessed Atai→Saboo case)
needs the destination tweet treated as a discoverable canonical source — the
same way arXiv/DOI/GitHub/Substack/Medium already are. This is the keystone:
once the extractor sees status URLs, the existing lint cross-link machinery
(_lint_body.py) auto-establishes the bidirectional reference for free.

Run from vault root:
    python3 -m pytest tests/test_canonical_source_xstatus.py -v
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LIB = ROOT / "bin" / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from canonical_source import extract_canonical_urls  # type: ignore  # noqa: E402


class XStatusExtraction(unittest.TestCase):
    def test_x_status_url_extracted_and_normalized(self):
        text = "Great thread: https://x.com/Saboo_Shubham_/status/2062220865643982875 — read it"
        urls = extract_canonical_urls(text)
        self.assertIn("https://x.com/Saboo_Shubham_/status/2062220865643982875", urls)

    def test_twitter_com_normalized_to_x_com(self):
        text = "see https://twitter.com/Saboo_Shubham_/status/2062220865643982875"
        urls = extract_canonical_urls(text)
        self.assertIn("https://x.com/Saboo_Shubham_/status/2062220865643982875", urls)

    def test_query_string_stripped(self):
        text = "https://x.com/ataiiam/status/2062236697534812299?s=12&t=abc"
        urls = extract_canonical_urls(text)
        self.assertIn("https://x.com/ataiiam/status/2062236697534812299", urls)

    def test_profile_and_i_links_not_extracted(self):
        # Profile links, /i/article, /i/web, and media hosts are NOT tweets.
        text = (
            "follow https://x.com/ataiiam and https://x.com/i/article/123 "
            "and https://x.com/i/web/status/999 and https://pic.twitter.com/abc"
        )
        urls = extract_canonical_urls(text)
        status_urls = [u for u in urls if "/status/" in u]
        # x.com/i/web/status/999 has "i" as the handle slot — i is a reserved
        # path, not a real handle; must NOT be surfaced as a tweet.
        self.assertNotIn("https://x.com/i/status/999", status_urls)
        self.assertEqual(status_urls, [])

    def test_dedup(self):
        text = (
            "https://x.com/a/status/123 and again "
            "https://twitter.com/a/status/123"
        )
        urls = extract_canonical_urls(text)
        self.assertEqual(
            [u for u in urls if u.endswith("/status/123")],
            ["https://x.com/a/status/123"],
        )


if __name__ == "__main__":
    unittest.main()
