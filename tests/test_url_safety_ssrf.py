"""SSRF guard (#242): URLs pointing at the local machine or a private network
must never be queued from captured-page content, nor fetched by any ingest
entry point.

Anchor: embed_discovery queued every <iframe src> that wasn't an analytics or
widget host, so a page embedding http://169.254.169.254/latest/meta-data/ got
that URL captured on the next ingest cycle. The same root cause reached the
inbox through every queue_canonical_urls caller, and the fetch entry points
checked only the scheme.

Run from vault root:
    python3 -m pytest tests/test_url_safety_ssrf.py -v
"""
from __future__ import annotations

import io
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import ExitStack, redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
LIB = ROOT / "bin" / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

import canonical_source  # type: ignore  # noqa: E402
import embed_discovery  # type: ignore  # noqa: E402

INTERNAL = [
    "http://localhost/admin",
    "http://localhost:8080/",
    "https://LOCALHOST./x",
    "http://foo.localhost/",
    "http://printer.local/",
    "http://metadata.google.internal/computeMetadata/v1/",
    "http://127.0.0.1/",
    "http://127.1/",
    "http://2130706433/",
    "http://0x7f000001/",
    "http://0177.0.0.1/",
    "http://0.0.0.0:3000/",
    "http://10.0.0.5/",
    "http://172.16.3.4/",
    "http://192.168.1.1/",
    "http://100.64.0.1/",
    "http://169.254.169.254/latest/meta-data/",
    "http://[::1]/",
    "http://[::ffff:127.0.0.1]/",
    "http://[::ffff:a9fe:a9fe]/",
    "http://[fe80::1]/",
    "http://[fd00::1]/",
    "http://user:pw@127.0.0.1/",
    "file:///etc/passwd",
    "ftp://example.com/",
    # Parser differentials (review of 0f0b8c6a): the resolver/fetcher must not
    # see a different host than this check does.
    "http://１２７.０.０.１/",
    "http://127。0。0。1/",
    "http://ｌｏｃａｌｈｏｓｔ/",
    "http://example.com\\@127.0.0.1/",
    "http://a@example.com@127.0.0.1/",
    "http://example.com\t.127.0.0.1/",
    "http://exa\x00mple.com/",
    "http://exa\nmple.com/",
    "http://[64:ff9b::7f00:1]/",
    "http://[64:ff9b::a9fe:a9fe]/latest/meta-data/",
    "https://user@example.com/",
    "http://%31%32%37.0.0.1/",
    "http://example.com%2f.127.0.0.1/",
]

PUBLIC = [
    "https://ironcurtain.dev/uofm-secrit/",
    "https://arxiv.org/abs/2401.00001",
    "http://8.8.8.8/",
    "https://[2606:4700:4700::1111]/",
    "https://localhost-tools.example.com/",
    "https://example.com/localhost",
]


class UrlSafetyUnit(unittest.TestCase):
    def test_internal_urls_refused(self):
        import url_safety  # type: ignore
        for u in INTERNAL:
            with self.subTest(url=u):
                self.assertTrue(url_safety.is_internal_url(u), u)

    def test_public_urls_allowed(self):
        import url_safety  # type: ignore
        for u in PUBLIC:
            with self.subTest(url=u):
                self.assertFalse(url_safety.is_internal_url(u), url_safety.internal_url_reason(u))


class _Vault(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kb-ssrf-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        (self.tmp / "inbox").mkdir()

    def url_new(self) -> set[str]:
        f = self.tmp / "inbox" / "url-new.txt"
        if not f.exists():
            return set()
        return {ln.strip() for ln in f.read_text().splitlines() if ln.strip()}


class ProducersNeverQueueInternal(_Vault):
    """Every page-derived URL reaches the inbox through queue_canonical_urls."""

    def test_queue_canonical_urls_drops_internal(self):
        queued = canonical_source.queue_canonical_urls(
            self.tmp, "https://example.com/page", INTERNAL + PUBLIC[:2]
        )
        self.assertEqual(queued, PUBLIC[:2])
        self.assertEqual(self.url_new(), set(PUBLIC[:2]))

    def test_embed_discovery_drops_internal_iframes(self):
        html = "".join(f'<iframe src="{u}"></iframe>' for u in INTERNAL)
        html += '<iframe src="https://ironcurtain.dev/uofm-secrit/"></iframe>'
        html += '<iframe src="/relative-deck/"></iframe>'
        self.assertEqual(
            embed_discovery.extract_embed_urls(html, "http://127.0.0.1/page"),
            ["https://ironcurtain.dev/uofm-secrit/"],
        )
        queued = embed_discovery.discover_and_queue(
            self.tmp, "https://example.com/page", html=html
        )
        # Relative srcs resolve against the container; a public container's
        # relative embed is a public URL and is kept.
        self.assertEqual(
            queued,
            ["https://ironcurtain.dev/uofm-secrit/", "https://example.com/relative-deck/"],
        )
        tsv = (self.tmp / "inbox" / "embed-sources.tsv").read_text()
        self.assertNotIn("169.254", tsv)
        self.assertNotIn("localhost", tsv)

    def test_lint_requeue_skips_internal_rows_already_in_tsv(self):
        """A vault written before the fix may already hold bad tsv rows."""
        import cmd_lint  # type: ignore
        (self.tmp / "bin").symlink_to(ROOT / "bin")
        for d in ("wiki/format/webpages", "raw/webpages/artifacts"):
            (self.tmp / d).mkdir(parents=True, exist_ok=True)
        (self.tmp / "inbox" / "embed-sources.tsv").write_text(
            "https://example.com/page\thttp://169.254.169.254/latest/meta-data/\n"
            "https://example.com/page\thttps://ironcurtain.dev/uofm-secrit/\n",
            encoding="utf-8",
        )
        cmd_lint.handle([], str(self.tmp))
        self.assertEqual(self.url_new(), {"https://ironcurtain.dev/uofm-secrit/"})


class FetchersRefuseInternal(_Vault):
    """Whoever put the URL in the inbox, the fetch entry points refuse it."""

    def test_unified_ingest_refuses(self):
        import unified_ingest  # type: ignore
        self._no_network()
        for u in ("http://169.254.169.254/latest/meta-data/", "http://localhost:8080/"):
            with self.subTest(url=u):
                with self.assertRaises(unified_ingest.UnifiedIngestError) as cm:
                    unified_ingest.ingest(unified_ingest.IngestInput(vault_root=self.tmp, url=u))
                self.assertIn("refusing to fetch", str(cm.exception))

    def _no_network(self):
        """Any fetch attempt fails the test instead of leaving the machine."""
        def boom(*a, **k):
            raise AssertionError(f"fetch attempted: {a[:1]!r}")
        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(mock.patch("urllib.request.urlopen", boom))
        stack.enter_context(mock.patch("subprocess.run", boom))
        stack.enter_context(mock.patch("subprocess.Popen", boom))

    def test_capture_refuses(self):
        import capture  # type: ignore
        self._no_network()
        err = io.StringIO()
        with redirect_stderr(err), redirect_stdout(io.StringIO()):
            try:
                rc = capture.run_capture(
                    "http://169.254.169.254/latest/meta-data/", vault_root=str(self.tmp)
                )
            except AssertionError as e:
                self.fail(str(e))
        self.assertNotEqual(rc, 0)
        self.assertIn("non-public", err.getvalue())

    def test_capture_rechecks_url_swapped_in_by_resolver(self):
        """A tweet's t.co destination (page-controlled) replaces `url` after the
        first check; it must be checked again before anything fetches it."""
        import capture  # type: ignore
        self._no_network()
        err = io.StringIO()
        with mock.patch.object(capture, "resolve_tweet",
                               return_value="http://169.254.169.254/latest/meta-data/"), \
                redirect_stderr(err), redirect_stdout(io.StringIO()):
            try:
                rc = capture.run_capture(
                    "https://x.com/someone/status/1234567890", vault_root=str(self.tmp)
                )
            except AssertionError as e:
                self.fail(str(e))
        self.assertNotEqual(rc, 0)
        self.assertIn("non-public", err.getvalue())

    def test_capture_checks_between_chained_resolvers(self):
        """A t.co destination that looks like a Reddit thread must be refused
        before resolve_reddit fetches it, not after."""
        import capture  # type: ignore
        self._no_network()
        reddit = mock.Mock(side_effect=AssertionError("resolve_reddit fetched an internal URL"))
        err = io.StringIO()
        with mock.patch.object(capture, "resolve_tweet",
                               return_value="http://10.0.0.1/reddit.com/r/x/comments/y"), \
                mock.patch.object(capture, "resolve_reddit", reddit), \
                redirect_stderr(err), redirect_stdout(io.StringIO()):
            try:
                rc = capture.run_capture(
                    "https://x.com/someone/status/1234567890", vault_root=str(self.tmp)
                )
            except AssertionError as e:
                self.fail(str(e))
        self.assertNotEqual(rc, 0)
        self.assertFalse(reddit.called)
        self.assertIn("non-public", err.getvalue())

    def test_ingest_url_refuses(self):
        import ingest_url  # type: ignore
        self._no_network()
        err = io.StringIO()
        with mock.patch.object(sys, "argv", ["ingest_url.py", "http://127.0.0.1:9/report.pdf"]), \
                mock.patch.dict(os.environ, {"KB_ROOT": str(self.tmp)}), \
                redirect_stderr(err), redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit) as cm:
                try:
                    ingest_url.main()
                except AssertionError as e:
                    self.fail(str(e))
        self.assertNotEqual(cm.exception.code, 0)
        self.assertIn("non-public", err.getvalue())


if __name__ == "__main__":
    unittest.main()
