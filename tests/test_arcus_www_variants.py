"""arcus_html._www_variants — www-retry for hosts that only serve with www.

Anchor (2026-06-27): canonicalize() strips `www.` for dedup, but provos.org only
serves WITH www (arcus exit 10 without it). The fetch layer retries the `www.`
variant so www-only hosts still capture, while dedup keeps the www-less canonical.

Run from vault root:
    python3 -m pytest tests/test_arcus_www_variants.py -v
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LIB = ROOT / "bin" / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

import arcus_html as ah  # type: ignore  # noqa: E402


class WwwVariants(unittest.TestCase):
    def test_bare_host_gets_www_retry(self):
        self.assertEqual(
            ah._www_variants("https://provos.org/p/x"),
            ["https://provos.org/p/x", "https://www.provos.org/p/x"],
        )

    def test_www_host_no_extra_variant(self):
        self.assertEqual(
            ah._www_variants("https://www.example.com/y"),
            ["https://www.example.com/y"],
        )

    def test_subdomain_gets_www_prefixed(self):
        # A non-www subdomain still gets a www. retry — cheap, harmless if 404.
        self.assertEqual(
            ah._www_variants("https://sub.example.com/z"),
            ["https://sub.example.com/z", "https://www.sub.example.com/z"],
        )

    def test_canonical_form_is_first(self):
        # The given (canonical, www-less) URL is always tried first so dedup
        # behaviour is unchanged; www is only the fallback.
        self.assertEqual(ah._www_variants("https://provos.org/a")[0],
                         "https://provos.org/a")


if __name__ == "__main__":
    unittest.main()
