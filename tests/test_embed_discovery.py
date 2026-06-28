"""embed_discovery — surface content-bearing <iframe> embeds as capturable sources.

Anchor bug (2026-06-27): provos.org/p/talks-ai-zero-days-and-invariants embeds
two reveal.js slide decks via cross-origin <iframe> (ironcurtain.dev). The browser
clipper can't read inside them and arcus strips the shells, so the decks were
never captured. These tests pin the extraction + queue behavior that fixes it.

Run from vault root:
    python3 -m pytest tests/test_embed_discovery.py -v
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

import embed_discovery as ed  # type: ignore  # noqa: E402

# The real provos.org embed shell (trimmed), including the GTM noscript iframe
# that MUST be dropped and the two reveal.js decks that MUST be kept.
PROVOS_HTML = """
<html><head><title>Two Talks</title></head><body>
<noscript><iframe src="https://www.googletagmanager.com/ns.html?id=GTM-NHMVG3WV"
    height="0" width="0" style="display:none"></iframe></noscript>
<figure class="slides-embed">
  <div class="slides-embed__frame" style="aspect-ratio: 16 / 10;">
    <iframe src="https://ironcurtain.dev/uofm-secrit/" title="LLM Security"
      loading="lazy" allowfullscreen allow="fullscreen"></iframe>
  </div>
</figure>
<figure class="slides-embed">
  <div class="slides-embed__frame">
    <iframe src="https://ironcurtain.dev/csa-zerodays/" title="Zero-Days"
      loading="lazy" allowfullscreen></iframe>
  </div>
</figure>
<iframe class="giscus-frame" src="https://giscus.app/en/widget?repo=x"></iframe>
</body></html>
"""

SOURCE = "https://www.provos.org/p/talks-ai-zero-days-and-invariants"


class ExtractEmbedUrls(unittest.TestCase):
    def test_keeps_content_decks_drops_widgets(self):
        urls = ed.extract_embed_urls(PROVOS_HTML, SOURCE)
        self.assertEqual(
            urls,
            [
                "https://ironcurtain.dev/uofm-secrit/",
                "https://ironcurtain.dev/csa-zerodays/",
            ],
        )

    def test_drops_gtm_and_giscus(self):
        urls = ed.extract_embed_urls(PROVOS_HTML, SOURCE)
        self.assertFalse(any("googletagmanager" in u for u in urls))
        self.assertFalse(any("giscus" in u for u in urls))

    def test_resolves_relative_src(self):
        html = '<iframe src="/decks/talk1/"></iframe>'
        urls = ed.extract_embed_urls(html, "https://example.com/posts/x")
        self.assertEqual(urls, ["https://example.com/decks/talk1/"])

    def test_drops_media_players(self):
        html = (
            '<iframe src="https://www.youtube.com/embed/abc123"></iframe>'
            '<iframe src="https://player.vimeo.com/video/9"></iframe>'
            '<iframe src="https://open.spotify.com/embed/episode/z"></iframe>'
            '<iframe src="https://reveal.example.org/deck/"></iframe>'
        )
        urls = ed.extract_embed_urls(html, "https://example.com/")
        self.assertEqual(urls, ["https://reveal.example.org/deck/"])

    def test_drops_non_http_schemes(self):
        html = (
            '<iframe src="about:blank"></iframe>'
            '<iframe src="data:text/html,<b>x</b>"></iframe>'
            "<iframe src=\"javascript:void(0)\"></iframe>"
            '<iframe src="https://ok.example/deck/"></iframe>'
        )
        urls = ed.extract_embed_urls(html, "https://example.com/")
        self.assertEqual(urls, ["https://ok.example/deck/"])

    def test_strips_fragment_and_dedups(self):
        html = (
            '<iframe src="https://ok.example/deck/#slide=1"></iframe>'
            '<iframe src="https://ok.example/deck/#slide=9"></iframe>'
        )
        urls = ed.extract_embed_urls(html, "https://example.com/")
        self.assertEqual(urls, ["https://ok.example/deck/"])

    def test_single_quoted_src(self):
        html = "<iframe src='https://ok.example/deck/'></iframe>"
        self.assertEqual(
            ed.extract_embed_urls(html, "https://example.com/"),
            ["https://ok.example/deck/"],
        )

    def test_no_iframe_returns_empty(self):
        self.assertEqual(ed.extract_embed_urls("<p>just text</p>", SOURCE), [])
        self.assertEqual(ed.extract_embed_urls("", SOURCE), [])


class DiscoverAndQueue(unittest.TestCase):
    def setUp(self):
        self.vault = Path(tempfile.mkdtemp(prefix="kb-embed-"))
        (self.vault / "inbox").mkdir(parents=True)
        self.addCleanup(shutil.rmtree, self.vault, ignore_errors=True)

    def _url_new(self) -> list[str]:
        f = self.vault / "inbox" / "url-new.txt"
        if not f.exists():
            return []
        return [ln.strip() for ln in f.read_text().splitlines() if ln.strip()]

    def test_queues_embeds_from_passed_html(self):
        queued = ed.discover_and_queue(self.vault, SOURCE, html=PROVOS_HTML)
        self.assertEqual(
            set(queued),
            {
                "https://ironcurtain.dev/uofm-secrit/",
                "https://ironcurtain.dev/csa-zerodays/",
            },
        )
        self.assertEqual(set(self._url_new()), set(queued))

    def test_records_container_to_embed_mapping(self):
        ed.discover_and_queue(self.vault, SOURCE, html=PROVOS_HTML)
        tsv = (self.vault / "inbox" / "embed-sources.tsv").read_text()
        self.assertIn(f"{SOURCE}\thttps://ironcurtain.dev/uofm-secrit/", tsv)
        self.assertIn(f"{SOURCE}\thttps://ironcurtain.dev/csa-zerodays/", tsv)

    def test_idempotent(self):
        ed.discover_and_queue(self.vault, SOURCE, html=PROVOS_HTML)
        second = ed.discover_and_queue(self.vault, SOURCE, html=PROVOS_HTML)
        self.assertEqual(second, [])  # already queued
        self.assertEqual(len(self._url_new()), 2)  # no dup lines

    def test_dedups_against_already_resolved(self):
        resolved = self.vault / "inbox" / "url-resolved.tsv"
        resolved.write_text(
            "captured\tDeck\thttps://ironcurtain.dev/uofm-secrit/\t2026-06-27\n"
        )
        queued = ed.discover_and_queue(self.vault, SOURCE, html=PROVOS_HTML)
        self.assertEqual(queued, ["https://ironcurtain.dev/csa-zerodays/"])

    def test_no_embeds_no_files(self):
        queued = ed.discover_and_queue(self.vault, SOURCE, html="<p>nothing</p>")
        self.assertEqual(queued, [])
        self.assertFalse((self.vault / "inbox" / "url-new.txt").exists())

    def test_soft_fails_on_fetch_failure(self):
        # No html passed + unreachable scheme → fetch_html returns None → []
        queued = ed.discover_and_queue(
            self.vault, "https://nonexistent.invalid./x", html=None
        )
        self.assertEqual(queued, [])


if __name__ == "__main__":
    unittest.main()
