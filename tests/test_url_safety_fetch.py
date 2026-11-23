"""SSRF at fetch time (#243): redirects and DNS rebinding.

#242 checks the URL Athena is handed, by name and literal only. Two holes
remain at fetch time, and this file pins both shut for every fetcher that
takes a page-controlled URL:

  - redirects: a public URL that 302s to http://169.254.169.254/ is followed
    by urllib's default opener and by `curl -L`;
  - DNS rebinding: a public-looking name that resolves to 10.0.0.1 passes the
    name check and is then connected to.

Plus the one fetcher #242 missed outright: asset_download fetched every
image URL in a captured page with only a scheme check.

No test leaves the machine: name resolution is faked, and the one real
server is on loopback (allowed only by patching the address check).

Run from vault root:
    python3 -m pytest tests/test_url_safety_fetch.py -v
"""
from __future__ import annotations

import http.server
import ipaddress
import shutil
import socket
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from contextlib import ExitStack
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
LIB = ROOT / "bin" / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

import url_safety  # type: ignore  # noqa: E402

REBIND_HOST = "rebind.attacker.example"  # "public" name -> private address
PUBLIC_HOST = "public.example"           # resolves to a public address


def _fake_getaddrinfo(table):
    real = socket.getaddrinfo

    def fake(host, port, *a, **k):
        if host in table:
            ip = table[host]
            fam = socket.AF_INET6 if ":" in ip else socket.AF_INET
            sa = (ip, port or 0, 0, 0) if fam == socket.AF_INET6 else (ip, port or 0)
            return [(fam, socket.SOCK_STREAM, 6, "", sa)]
        if host in ("127.0.0.1", "localhost"):
            return real(host, port, *a, **k)
        raise socket.gaierror(socket.EAI_NONAME, f"fake DNS: no such host {host}")
    return fake


class _Redirector(http.server.BaseHTTPRequestHandler):
    target = ""

    def do_GET(self):  # noqa: N802
        if self.path == "/ok":
            body = b"<html>fine</html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(302)
        self.send_header("Location", type(self).target)
        self.end_headers()

    do_HEAD = do_GET

    def log_message(self, *a):
        pass


class _Base(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(mock.patch(
            "socket.getaddrinfo",
            _fake_getaddrinfo({REBIND_HOST: "10.0.0.1", PUBLIC_HOST: "93.184.215.14"}),
        ))
        self.connects: list = []
        real_cc = socket.create_connection

        def watch(addr, *a, **k):
            self.connects.append(addr[0])
            if addr[0] != "127.0.0.1":
                raise AssertionError(f"connected to {addr[0]!r}")
            return real_cc(addr, *a, **k)
        self.stack.enter_context(mock.patch("socket.create_connection", watch))

    def loopback_server(self, target: str) -> str:
        """Serve a 302 to `target` on loopback; loopback itself is allowed."""
        handler = type("H", (_Redirector,), {"target": target})
        srv = http.server.HTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        real = url_safety._address_is_internal
        self.stack.enter_context(mock.patch.object(
            url_safety, "_address_is_internal",
            lambda ip: ip != ipaddress.ip_address("127.0.0.1") and real(ip),
        ))
        self.stack.enter_context(mock.patch.object(
            url_safety, "internal_url_reason",
            _allow_loopback_reason(url_safety.internal_url_reason),
        ))
        return f"http://127.0.0.1:{srv.server_port}"

    def assertRefused(self, exc):
        cause = getattr(exc, "reason", exc)
        self.assertTrue(
            isinstance(exc, url_safety.UnsafeURLError) or isinstance(cause, url_safety.UnsafeURLError),
            f"not refused by url_safety: {exc!r}",
        )


def _allow_loopback_reason(real):
    def reason(url):
        if url.startswith("http://127.0.0.1:"):
            return ""
        return real(url)
    return reason


class SafeUrlopen(_Base):
    def test_rebinding_name_never_connected(self):
        with self.assertRaises(urllib.error.URLError) as cm:
            url_safety.safe_urlopen(f"http://{REBIND_HOST}/latest/meta-data/", timeout=3)
        self.assertRefused(cm.exception)
        self.assertEqual(self.connects, [])

    def test_literal_internal_refused_before_dns(self):
        with self.assertRaises(urllib.error.URLError) as cm:
            url_safety.safe_urlopen("http://169.254.169.254/latest/meta-data/", timeout=3)
        self.assertRefused(cm.exception)
        self.assertEqual(self.connects, [])

    def test_redirect_to_metadata_literal_not_followed(self):
        base = self.loopback_server("http://169.254.169.254/latest/meta-data/")
        with self.assertRaises(urllib.error.URLError) as cm:
            url_safety.safe_urlopen(base + "/start", timeout=3)
        self.assertRefused(cm.exception)
        self.assertEqual(self.connects, ["127.0.0.1"])

    def test_redirect_to_rebinding_name_not_followed(self):
        base = self.loopback_server(f"http://{REBIND_HOST}/admin")
        with self.assertRaises(urllib.error.URLError) as cm:
            url_safety.safe_urlopen(base + "/start", timeout=3)
        self.assertRefused(cm.exception)
        self.assertEqual(self.connects, ["127.0.0.1"])

    def test_public_fetch_still_works(self):
        base = self.loopback_server("unused")
        with url_safety.safe_urlopen(base + "/ok", timeout=3) as resp:
            self.assertEqual(resp.read(), b"<html>fine</html>")

    def test_request_object_accepted(self):
        base = self.loopback_server("unused")
        req = urllib.request.Request(base + "/ok", headers={"User-Agent": "t"})
        with url_safety.safe_urlopen(req, timeout=3) as resp:
            self.assertEqual(resp.status, 200)


class FetchersUseSafeOpen(_Base):
    """Every urllib fetcher of a page-controlled URL refuses a rebinding host
    without connecting (pre-fix: each one connected to 10.0.0.1)."""

    def test_embed_discovery_fetch_html(self):
        import embed_discovery  # type: ignore
        self.assertIsNone(embed_discovery.fetch_html(f"https://{REBIND_HOST}/page"))
        self.assertEqual(self.connects, [])

    def test_url_detect_head(self):
        import url_detect  # type: ignore
        url_detect.detect_type(f"https://{REBIND_HOST}/report.pdf")
        self.assertEqual(self.connects, [])

    def test_ingest_url_download(self):
        import ingest_url  # type: ignore
        tmp = Path(tempfile.mkdtemp(prefix="kb-ssrf-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        ok, reason = ingest_url._download_with_reason(
            f"https://{REBIND_HOST}/report.pdf", str(tmp / "x.pdf"))
        self.assertFalse(ok)
        self.assertEqual(self.connects, [])

    def test_ingest_url_drive_filename_uses_safe_open(self):
        import ingest_url  # type: ignore
        with mock.patch.object(ingest_url, "safe_urlopen",
                               side_effect=urllib.error.URLError("x")) as so:
            ingest_url._drive_filename("https://drive.google.com/file/d/abc/view")
        self.assertTrue(so.called)


class AssetDownload(_Base):
    """#242 sibling: page image URLs were fetched with only a scheme check."""

    def setUp(self):
        super().setUp()
        self.vault = Path(tempfile.mkdtemp(prefix="kb-ssrf-"))
        self.addCleanup(shutil.rmtree, self.vault, ignore_errors=True)
        (self.vault / "inbox").mkdir()

    def test_internal_image_literal_not_fetched_or_kept(self):
        import asset_download  # type: ignore
        md = "a\n\n![meta](http://169.254.169.254/latest/meta-data/x.png)\n\nb\n"
        out = asset_download.download_assets(md, "page", self.vault)
        self.assertEqual(self.connects, [])
        self.assertNotIn("169.254", out)
        retry = self.vault / "inbox" / "asset-retry.tsv"
        self.assertFalse(retry.exists() and "169.254" in retry.read_text())

    def test_rebinding_image_not_fetched(self):
        import asset_download  # type: ignore
        md = f"![x](https://{REBIND_HOST}/x.png)\n"
        asset_download.download_assets(md, "page", self.vault)
        self.assertEqual(self.connects, [])


class CaptureCurl(unittest.TestCase):
    """capture._curl: curl does its own DNS and its own -L, so the check
    moves into a hop loop: resolve, check, pin with --connect-to, no -L,
    read the redirect target, check it, repeat."""

    def setUp(self):
        import capture  # type: ignore
        self.capture = capture
        self.calls: list[list[str]] = []
        p = mock.patch("socket.getaddrinfo",
                       _fake_getaddrinfo({REBIND_HOST: "10.0.0.1", PUBLIC_HOST: "93.184.215.14"}))
        p.start()
        self.addCleanup(p.stop)

    def fake_run(self, redirects: dict[str, str]):
        def run(cmd, **kw):
            self.calls.append(cmd)
            url = next(a for a in cmd if a.startswith(("http://", "https://")))
            nxt = redirects.get(url, "")
            hdr = cmd[cmd.index("-D") + 1]
            Path(hdr).write_text(
                f"HTTP/1.1 302 Found\r\nLocation: {nxt}\r\n\r\n" if nxt
                else "HTTP/1.1 200 OK\r\nContent-Type: text/html\r\n\r\n")
            import subprocess
            return subprocess.CompletedProcess(cmd, 0, stdout=f"body of {url}", stderr="")
        return mock.patch.object(self.capture, "_run", run)

    def test_rebinding_host_never_curled(self):
        with self.fake_run({}):
            out = self.capture._curl(["-sL", "--max-time", "10", f"https://{REBIND_HOST}/x"])
        self.assertEqual(out, "")
        self.assertEqual(self.calls, [])

    def test_redirect_to_internal_not_followed(self):
        start = f"https://{PUBLIC_HOST}/a"
        with self.fake_run({start: "http://169.254.169.254/latest/meta-data/"}):
            out = self.capture._curl(["-sL", "--max-time", "10", start])
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(out, "")

    def test_redirect_to_rebinding_host_not_followed(self):
        start = f"https://{PUBLIC_HOST}/a"
        with self.fake_run({start: f"https://{REBIND_HOST}/b"}):
            self.capture._curl(["-sL", start])
        self.assertEqual(len(self.calls), 1)

    def test_public_redirect_followed_and_pinned(self):
        start = f"https://{PUBLIC_HOST}/a"
        final = f"https://{PUBLIC_HOST}/b"
        with self.fake_run({start: final}):
            out = self.capture._curl(["-sfL", "--max-time", "10", start])
        self.assertEqual(out, f"body of {final}")
        self.assertEqual(len(self.calls), 2)
        for cmd in self.calls:
            self.assertNotIn("-L", " ".join(a for a in cmd if a.startswith("-") and not a.startswith("--")))
            self.assertIn("--connect-to", cmd)
            self.assertIn("::93.184.215.14:443", cmd)
            self.assertNotIn("--resolve", cmd)

    def test_head_mode_keeps_every_hop_headers(self):
        """health_check parses the LAST status line of `curl -sIL` output."""
        start = f"https://{PUBLIC_HOST}/a"
        final = f"https://{PUBLIC_HOST}/b"
        with self.fake_run({start: final}):
            out = self.capture._curl(["-sI", "--max-time", "10", "-L", start])
        self.assertEqual(out, f"body of {start}body of {final}")

    def test_no_follow_call_is_still_pinned(self):
        """`curl -sI t.co/...` without -L: one request, still resolve-checked."""
        with self.fake_run({}):
            out = self.capture._curl(["-sI", "--max-time", "5", f"https://{REBIND_HOST}/t"])
        self.assertEqual(out, "")
        self.assertEqual(self.calls, [])

    def test_redirect_cap(self):
        hops = {f"https://{PUBLIC_HOST}/{i}": f"https://{PUBLIC_HOST}/{i + 1}" for i in range(50)}
        with self.fake_run(hops):
            out = self.capture._curl(["-sL", f"https://{PUBLIC_HOST}/0"])
        self.assertEqual(out, "")
        self.assertLessEqual(len(self.calls), url_safety.MAX_REDIRECTS + 1)


class Proxied(_Base):
    """With a proxy configured the proxy resolves the target; the proxy's own
    (often loopback) address is allowed, the target is still resolve-checked
    up front and at every redirect."""

    def test_rebinding_target_refused_through_proxy(self):
        with mock.patch.dict("os.environ", {"http_proxy": "http://127.0.0.1:3128",
                                            "HTTP_PROXY": "http://127.0.0.1:3128"}):
            with self.assertRaises(urllib.error.URLError) as cm:
                url_safety.safe_urlopen(f"http://{REBIND_HOST}/", timeout=3)
        self.assertRefused(cm.exception)
        self.assertEqual(self.connects, [])

    def test_redirect_through_proxy_to_rebinding_target_refused(self):
        base = self.loopback_server(f"http://{REBIND_HOST}/admin")
        proxy = base  # the loopback redirector doubles as the "proxy"
        with mock.patch.dict("os.environ", {"http_proxy": proxy, "HTTP_PROXY": proxy,
                                            "no_proxy": "", "NO_PROXY": ""}):
            with self.assertRaises(urllib.error.URLError) as cm:
                url_safety.safe_urlopen(f"http://{PUBLIC_HOST}/start", timeout=3)
        self.assertRefused(cm.exception)

    def test_unproxied_target_still_pinned_when_a_proxy_exists(self):
        with mock.patch.dict("os.environ", {"http_proxy": "http://127.0.0.1:3128",
                                            "HTTP_PROXY": "http://127.0.0.1:3128",
                                            "no_proxy": REBIND_HOST, "NO_PROXY": REBIND_HOST}):
            with self.assertRaises(urllib.error.URLError) as cm:
                url_safety.safe_urlopen(f"http://{REBIND_HOST}/", timeout=3)
        self.assertRefused(cm.exception)
        self.assertEqual(self.connects, [])


class BadPort(unittest.TestCase):
    """A port urlsplit can't parse must be a refusal, not a ValueError that
    escapes _curl / the arcus adapters and aborts the whole capture."""

    def test_bad_ports_refused_not_raised(self):
        import capture  # type: ignore
        for u in ("http://example.com:abc/", "http://example.com:99999/"):
            with self.subTest(url=u):
                self.assertTrue(url_safety.internal_url_reason(u))
                self.assertTrue(url_safety.resolved_url_reason(u))
                self.assertEqual(capture._curl(["-sL", u]), "")


class VideoYtDlp(_Base):
    """detect_type routes any URL containing "youtu.be/" to video; yt-dlp
    must only ever see YouTube's own hosts."""

    def test_youtube_host_check(self):
        import capture  # type: ignore
        self.assertTrue(capture._is_youtube_host("https://www.youtube.com/watch?v=x"))
        self.assertTrue(capture._is_youtube_host("https://youtu.be/x"))
        self.assertFalse(capture._is_youtube_host("https://evil.example/r?x=youtu.be/a"))
        self.assertFalse(capture._is_youtube_host("https://youtube.com.evil.example/watch?v=x"))

    def test_non_youtube_host_never_reaches_yt_dlp(self):
        import capture  # type: ignore
        ran = []
        with mock.patch.object(capture, "_run", lambda cmd, **k: ran.append(cmd) or (_ for _ in ()).throw(FileNotFoundError())), \
                mock.patch.object(capture, "_curl", lambda *a, **k: ""):
            tmp = Path(tempfile.mkdtemp(prefix="kb-ssrf-"))
            self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
            try:
                capture.handle_video("https://evil.example/r?x=youtu.be/a", "s", "2026-01-01",
                                     {"videos": str(tmp)}, {})
            except (SystemExit, Exception):
                pass
        self.assertFalse(any("yt_dlp" in " ".join(map(str, c)) for c in ran), ran)

    def test_rebinding_video_url_refused(self):
        import capture  # type: ignore
        tmp = Path(tempfile.mkdtemp(prefix="kb-ssrf-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        with mock.patch.object(capture, "_run", side_effect=AssertionError("ran a command")):
            with self.assertRaises(SystemExit):
                capture.handle_video(f"https://{REBIND_HOST}/?youtu.be/x", "s", "2026-01-01",
                                     {"videos": str(tmp)}, {})


class ArcusHandoff(_Base):
    """arcus fetches on its own; Athena refuses a privately-resolving name
    before handing it over (the in-arcus redirect check is arcus's)."""

    def test_html_refused_before_arcus(self):
        import arcus_html  # type: ignore
        with mock.patch.dict(sys.modules, {"arcus.provider_runtime": None}):
            with self.assertRaises(arcus_html.ArcusHtmlError) as cm:
                arcus_html.extract_html_url(url=f"https://{REBIND_HOST}/post")
        self.assertIn("non-public", str(cm.exception))
        with self.assertRaises(arcus_html.ArcusHtmlError) as cm:
            arcus_html.extract_html_url(url=f"https://{REBIND_HOST}/post", deep=True)
        self.assertIn("non-public", str(cm.exception))

    def test_video_refused_before_arcus(self):
        import arcus_video  # type: ignore
        with self.assertRaises(arcus_video.ArcusVideoError) as cm:
            arcus_video.ingest_youtube_url(vault=Path(tempfile.gettempdir()),
                                           url=f"https://{REBIND_HOST}/youtube.com/watch?v=x")
        self.assertIn("non-public", str(cm.exception))

    def test_lookup_failure_is_not_a_refusal(self):
        self.assertEqual(url_safety.resolved_url_reason("https://nxdomain.example/"), "")
        self.assertEqual(url_safety.resolved_url_reason(f"https://{PUBLIC_HOST}/"), "")


class CaptureCurlReal(_Base):
    """The real curl binary against a loopback redirector: --resolve, -D and
    the hop loop work with the curl that ships, not just with a fake."""

    def setUp(self):
        super().setUp()
        if not shutil.which("curl"):
            self.skipTest("curl not installed")
        import capture  # type: ignore
        self.capture = capture

    def test_follows_public_redirect(self):
        base = self.loopback_server("/ok")  # relative Location, made absolute
        out = self.capture._curl(["-sL", "--max-time", "5", base + "/start"])
        self.assertEqual(out, "<html>fine</html>")

    def test_refuses_redirect_to_metadata(self):
        base = self.loopback_server("http://169.254.169.254/latest/meta-data/")
        out = self.capture._curl(["-sL", "--max-time", "5", base + "/start"])
        self.assertEqual(out, "")

    def test_head_without_follow_reports_the_302(self):
        base = self.loopback_server("http://169.254.169.254/")
        out = self.capture._curl(["-sI", "--max-time", "5", base + "/start"])
        self.assertIn("302", out.splitlines()[0])


if __name__ == "__main__":
    unittest.main()
