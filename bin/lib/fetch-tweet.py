#!/usr/bin/env python3
"""Fetch X.com tweet (text + images + resolved t.co links + paragraph
breaks + reconstructed URLs) via Playwright.

Usage: python3 fetch-tweet.py <tweet_url>

Output convention:
  stdout — tweet text body (paragraph breaks restored, t.co links
           resolved, URLs reconstructed)
  stderr — `IMG: <url>` lines, one per attached image, large size

This module is a thin shim around fetch-page.fetch_x_tweet. The shim
exists for backward compatibility with bin/kb-capture which used to call
fetch-tweet.py for text-only extraction. The implementation now lives in
fetch-page.py (see fetch_x_tweet) so all X.com capture paths share one
canonical extraction.

Exit codes:
  0 — success (text on stdout, IMG: lines on stderr)
  1 — failure (error message on stderr)
"""
import os
import sys
import importlib.util


def _load_fetch_page():
    here = os.path.dirname(os.path.abspath(__file__))
    spec = importlib.util.spec_from_file_location("fp", os.path.join(here, "fetch-page.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 fetch-tweet.py <tweet_url>", file=sys.stderr)
        sys.exit(1)
    url = sys.argv[1]
    try:
        fp = _load_fetch_page()
        result = fp.fetch_x_tweet(url)
    except ImportError:
        print("Playwright not installed", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    if not result['text']:
        print("No content found", file=sys.stderr)
        sys.exit(1)
    print(result['text'])
    for img in result['images']:
        print(f"IMG: {img}", file=sys.stderr)
    sys.exit(0)


if __name__ == "__main__":
    main()
