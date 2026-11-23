"""arcus fetches nothing Athena's URL policy refuses: redirects included.

#243 left one hole, named in url_safety's docstring: arcus (urllib,
Playwright, yt-dlp) fetches on its own, so a public page that redirects to
http://169.254.169.254/ was followed inside arcus. Athena only checked the URL
it handed over. arcus 0.7.1 takes a caller policy (set_url_guard) and applies
it to every hop and every resolved address; Athena installs its own.

The rules:
  1. arcus_url_policy returns None for a public URL or a public IP literal
     (arcus calls it with scheme://<ip>:<port>/ for every resolved address)
     and RAISES for a refused one: arcus treats any non-None return as a
     refusal, so a predicate would refuse everything.
  2. Every Athena path into arcus installs it process-wide (set_url_guard, not
     the thread-scoped form), with safe_urlopen as arcus's urllib opener.
  3. Integration: a local page that redirects (302, 2-hop chain, meta refresh,
     JS location) to a refused address never reaches it, on every path that
     goes through arcus (html, html deep, x/tweet, youtube) and on the PDF
     download. The target's hit counter is the evidence.

The "refused address" is a loopback server on its own port: the test policy
refuses that port and lets the origin's port through. Nothing leaves the
machine.

Run from vault root (browser cases need Playwright's Chromium):
    python3 -m pytest tests/test_arcus_url_guard.py -v
"""
from __future__ import annotations

import http.server
import shutil
import sys
import tempfile
import threading
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest import mock
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent
LIB = ROOT / "bin" / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

import url_safety  # type: ignore  # noqa: E402

try:
    from arcus.provider_runtime import url_guard as arcus_guard  # type: ignore
    HAVE_GUARD = hasattr(arcus_guard, "set_url_guard")
except ImportError:
    arcus_guard = None
    HAVE_GUARD = False

NOPROXY_ENV: dict = {}
PAGE_TEXT = " ".join(["Athena integration test paragraph with enough words."] * 40)


class _Target(http.server.BaseHTTPRequestHandler):
    hits: list[str] = []

    def do_GET(self):  # noqa: N802
        type(self).hits.append(self.path)
        body = b"<html><body>SECRET internal response</body></html>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_HEAD = do_GET

    def log_message(self, *a):
        pass


class _Origin(http.server.BaseHTTPRequestHandler):
    target = ""   # http://127.0.0.1:<target port>
    hits: list[str] = []

    def _send(self, code, body=b"", ctype="text/html", headers=()):
        self.send_response(code)
        for k, v in headers:
            self.send_header(k, v)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        type(self).hits.append(self.path)
        t = type(self).target
        p = self.path.split("?", 1)[0]
        if p.startswith("/r302"):
            return self._send(302, headers=[("Location", t + "/from-302")])
        if p.startswith("/hop1"):
            return self._send(302, headers=[("Location", "/hop2")])
        if p.startswith("/hop2"):
            return self._send(302, headers=[("Location", t + "/from-chain")])
        if p.startswith("/meta"):
            html = (f'<html><head><title>Meta</title><meta http-equiv="refresh" '
                    f'content="0; url={t}/from-meta"></head><body><p>{PAGE_TEXT}</p>'
                    f'</body></html>')
            return self._send(200, html.encode())
        if p.startswith("/js"):
            html = (f"<html><head><title>JS</title></head><body><p>{PAGE_TEXT}</p>"
                    f"<script>location.href='{t}/from-js';</script></body></html>")
            return self._send(200, html.encode())
        if p.startswith("/pdf"):
            return self._send(302, headers=[("Location", t + "/from-pdf.pdf")])
        return self._send(404, b"no")

    do_HEAD = do_GET

    def log_message(self, *a):
        pass


def _serve(handler):
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


class _Harness(unittest.TestCase):
    """Origin + target servers, and a policy that refuses only the target."""

    def setUp(self):
        self.target = _serve(_Target)
        self.addCleanup(self.target.server_close)
        self.addCleanup(self.target.shutdown)
        self.tport = self.target.server_address[1]
        _Target.hits = []
        _Origin.hits = []
        _Origin.target = f"http://127.0.0.1:{self.tport}"
        self.origin = _serve(_Origin)
        self.addCleanup(self.origin.server_close)
        self.addCleanup(self.origin.shutdown)
        self.base = f"http://127.0.0.1:{self.origin.server_address[1]}"

        real = url_safety.internal_url_reason
        tport = self.tport

        def test_reason(url):
            parts = urlsplit(url)
            if parts.hostname == "127.0.0.1":
                return "test: the refused address" if parts.port == tport else ""
            return real(url)

        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(mock.patch.object(url_safety, "internal_url_reason", test_reason))
        # Loopback passes the connect-time address check; the port decides.
        stack.enter_context(mock.patch.object(url_safety, "_address_is_internal", lambda ip: False))
        stack.enter_context(mock.patch.dict("os.environ", NOPROXY_ENV))
        if HAVE_GUARD:
            self.addCleanup(arcus_guard.set_url_guard, None)
        self.vault = Path(tempfile.mkdtemp(prefix="athena-guard-"))
        self.addCleanup(shutil.rmtree, self.vault, ignore_errors=True)

    def assertTargetNeverHit(self, what):
        # A capture that never reached the origin (no Chromium, a crash)
        # proves nothing: it would pass with or without the guard.
        self.assertTrue(_Origin.hits, f"{what}: the capture never fetched the origin page")
        self.assertEqual(_Target.hits, [], f"{what}: arcus fetched the refused address")


@unittest.skipUnless(HAVE_GUARD, "arcus-provider-runtime >= 0.7.1 not installed")
class PolicyContract(unittest.TestCase):
    def test_allows_public_names_and_ip_literals(self):
        for url in ("https://example.com/a", "http://93.184.216.34:80/",
                    "https://[2606:4700:4700::1111]:443/"):
            self.assertIsNone(url_safety.arcus_url_policy(url), url)

    def test_raises_to_refuse(self):
        for url in ("http://169.254.169.254/latest/meta-data/", "http://127.0.0.1:8080/",
                    "http://[::1]:80/", "http://10.0.0.1:80/", "http://localhost/",
                    "file:///etc/passwd"):
            with self.assertRaises(Exception, msg=url):
                url_safety.arcus_url_policy(url)

    def test_installed_process_wide_with_safe_urlopen(self):
        self.addCleanup(arcus_guard.set_url_guard, None)
        url_safety.install_arcus_guard()
        guard, opener = arcus_guard._default
        self.assertIs(guard, url_safety.arcus_url_policy)
        self.assertIs(opener, url_safety.safe_urlopen)
        # Visible from a thread that never entered a scope (pools, workers).
        seen = []
        t = threading.Thread(target=lambda: seen.append(arcus_guard.get_url_guard()))
        t.start()
        t.join()
        self.assertIs(seen[0], url_safety.arcus_url_policy)


@unittest.skipUnless(HAVE_GUARD, "arcus-provider-runtime >= 0.7.1 not installed")
class HtmlPath(_Harness):
    """arcus_html.extract_html_url, the Factory path (kb add <url>)."""

    def _run(self, path):
        import arcus_html  # type: ignore
        try:
            arcus_html.extract_html_url(url=self.base + path)
        except arcus_html.ArcusHtmlError:
            pass  # a refusal is a failed capture; what matters is the target

    def test_302(self):
        self._run("/r302")
        self.assertTargetNeverHit("html 302")

    def test_two_hop_chain(self):
        self._run("/hop1")
        self.assertTargetNeverHit("html 2-hop chain")

    def test_meta_refresh(self):
        self._run("/meta")
        self.assertTargetNeverHit("html meta refresh")

    def test_js_location(self):
        self._run("/js")
        self.assertTargetNeverHit("html JS location")


@unittest.skipUnless(HAVE_GUARD, "arcus-provider-runtime >= 0.7.1 not installed")
class HtmlDeepPath(_Harness):
    """extract_html_url(deep=True): arcus's Playwright fetch_page directly."""

    def _run(self, path):
        import arcus_html  # type: ignore
        try:
            arcus_html.extract_html_url(url=self.base + path, deep=True)
        except Exception as e:  # noqa: BLE001
            self.assertNotIsInstance(e, (AssertionError,))

    def test_302(self):
        self._run("/r302")
        self.assertTargetNeverHit("deep 302")

    def test_js_location(self):
        self._run("/js")
        self.assertTargetNeverHit("deep JS location")

    def test_meta_refresh(self):
        self._run("/meta")
        self.assertTargetNeverHit("deep meta refresh")


@unittest.skipUnless(HAVE_GUARD, "arcus-provider-runtime >= 0.7.1 not installed")
class TweetPath(_Harness):
    """The x/tweet branch of the deep path: arcus's fetch_x_tweet browser.
    It only runs for x.com URLs, so _is_x_url is made to accept the origin."""

    def _run(self, path):
        import arcus_html  # type: ignore
        from arcus.provider_runtime.providers.html import _athena_fetch_page as fp  # type: ignore
        with mock.patch.object(fp, "_is_x_url", lambda u: True):
            try:
                arcus_html.extract_html_url(url=self.base + path, deep=True)
            except Exception:  # noqa: BLE001
                pass

    def test_302(self):
        self._run("/r302")
        self.assertTargetNeverHit("tweet 302")

    def test_js_location(self):
        self._run("/js")
        self.assertTargetNeverHit("tweet JS location")


@unittest.skipUnless(HAVE_GUARD, "arcus-provider-runtime >= 0.7.1 not installed")
class YoutubePath(_Harness):
    """arcus_video.ingest_youtube_url: yt-dlp through arcus. The provider only
    takes YouTube URLs, so its parser is made to accept the origin."""

    def _run(self, path):
        import arcus_video  # type: ignore
        from arcus.provider_runtime.providers.youtube import youtube as yt  # type: ignore
        with mock.patch.object(yt, "parse_youtube_url", lambda raw: "abcdefghijk"):
            try:
                arcus_video.ingest_youtube_url(vault=self.vault, url=self.base + path)
            except arcus_video.ArcusVideoError:
                pass

    def test_302(self):
        self._run("/r302")
        self.assertTargetNeverHit("youtube 302")

    def test_two_hop_chain(self):
        self._run("/hop1")
        self.assertTargetNeverHit("youtube 2-hop chain")


class _NoProxyEnv:
    """The same captures with NO_PROXY=* in the environment (common on
    corporate machines). A fetcher that honours it skips arcus's guard proxy."""

    def setUp(self):
        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(mock.patch.dict("os.environ", {"NO_PROXY": "*", "no_proxy": "*"}))
        super().setUp()


class YoutubePathNoProxy(_NoProxyEnv, YoutubePath):
    # Witnessed 2026-11-22 on arcus 0.7.1: yt-dlp honoured NO_PROXY (and, on
    # the Max, macOS's proxy exceptions) and bypassed arcus's guard proxy; the
    # target was hit twice. Fixed in arcus 0.7.2: a hard assertion since.
    #
    # yt-dlp only consults NO_PROXY in its legacy urllib handler; with the
    # requests handler (the default when `requests` is installed, as on the
    # Pro) the bypass never happened, so the test passed on 0.7.1 there. Force
    # the urllib handler so the case is red on an unfixed arcus on ANY machine
    # (PGM 11-22; gauntlet did the same for F-AR-008). That is what makes it
    # usable as loop-verify-pip's red-first proof.
    def _run(self, path):
        from arcus.provider_runtime.providers.youtube import ytdlp_adapter  # type: ignore
        real = ytdlp_adapter.YoutubeDL

        def legacy_handler(params=None, *a, **k):
            params = dict(params or {})
            params["compat_opts"] = set(params.get("compat_opts") or ()) | {"prefer-legacy-http-handler"}
            return real(params, *a, **k)

        with mock.patch.object(ytdlp_adapter, "YoutubeDL", legacy_handler):
            super()._run(path)


class HtmlPathNoProxy(_NoProxyEnv, HtmlPath):
    pass


@unittest.skipUnless(HAVE_GUARD, "arcus-provider-runtime >= 0.7.1 not installed")
class PdfDownload(_Harness):
    """The PDF URL download (ingest_url) is Athena's own safe_urlopen."""

    def test_302(self):
        import ingest_url  # type: ignore
        ok, _ = ingest_url._download_with_reason(
            self.base + "/pdf", str(self.vault / "x.pdf"), timeout=10, require_pdf=True)
        self.assertFalse(ok)
        self.assertTargetNeverHit("pdf download 302")


if __name__ == "__main__":
    unittest.main()
