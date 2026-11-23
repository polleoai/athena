"""An arcus failure is never silent, and a fallback capture says so.

Witnessed 2026-11-22: the Max had no Playwright Chromium (missing since the
2026-10 migration), so every arcus browser capture failed with "Executable
doesn't exist". Nobody saw it for about two months:
  - the adapters silence arcus's fds and raised only "arcus extraction failed
    (exit 10)", so arcus's own reason (its stub .json `error`) was dropped;
  - capture.py printed "arcus returned insufficient content" and quietly used
    oembed text, a title-only placeholder, the abstract, or inline yt-dlp;
  - the raw it wrote looked like any other capture.

The rules:
  1. Every adapter's error carries arcus's reason (html, deep, tweet, file,
     video).
  2. A capture that falls back prints exactly one WARN line naming the reason
     and what it used instead...
  3. ...and its raw note carries `extracted_via_fallback: "<using>: <reason>"`
     (every raw writer in capture.py).
  4. A failed direct-file capture dies with the reason, not a generic line.
  5. lint lists raws captured through a fallback (gated: oracle parity).

Offline: arcus runs against loopback servers, the browser is "missing" via
PLAYWRIGHT_BROWSERS_PATH, and capture's helpers are faked.

Run from vault root:
    python3 -m pytest tests/test_arcus_fallback_visible.py -v
"""
from __future__ import annotations

import http.server
import importlib.util
import io
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from contextlib import ExitStack, redirect_stdout
from importlib.machinery import SourceFileLoader
from pathlib import Path
from unittest import mock
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent
LIB = ROOT / "bin" / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

import url_safety  # type: ignore  # noqa: E402

try:
    import arcus.provider_runtime  # type: ignore  # noqa: F401
    HAVE_ARCUS = True
except ImportError:
    HAVE_ARCUS = False

NO_BROWSER = "Executable doesn't exist"
ARCUS_STDERR = ("arcus_html: arcus extraction failed (exit 10) for https://x.com/a/status/1: "
                "fetch_page raised: BrowserType.launch: Executable doesn't exist at "
                "/home/someone/.cache/ms-playwright/chromium_headless_shell-1223/x\n")


def _load_capture():
    loader = SourceFileLoader("capture_fallback_mod", str(LIB / "capture.py"))
    spec = importlib.util.spec_from_loader("capture_fallback_mod", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


CAP = _load_capture()


class _Page(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        if self.path.startswith("/notfound"):
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        body = ("<html><head><title>T</title></head><body><p>"
                + "words " * 200 + "</p></body></html>").encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


class _Loopback(unittest.TestCase):
    """A loopback page arcus may fetch (the policy lets loopback through here)."""

    def setUp(self):
        self.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Page)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.addCleanup(self.srv.server_close)
        self.addCleanup(self.srv.shutdown)
        self.base = f"http://127.0.0.1:{self.srv.server_address[1]}"
        real = url_safety.internal_url_reason
        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(mock.patch.object(
            url_safety, "internal_url_reason",
            lambda u: "" if urlsplit(u).hostname == "127.0.0.1" else real(u)))
        stack.enter_context(mock.patch.object(url_safety, "_address_is_internal", lambda ip: False))
        # The browser is "missing", as on the Max from 2026-10 to 11-22.
        stack.enter_context(mock.patch.dict(os.environ, {"PLAYWRIGHT_BROWSERS_PATH": "/nonexistent-athena"}))
        self.tmp = Path(tempfile.mkdtemp(prefix="athena-fb-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)


# ── 1. adapters carry arcus's reason ─────────────────────────────────────────
@unittest.skipUnless(HAVE_ARCUS, "arcus-provider-runtime not installed")
class AdapterReasons(_Loopback):
    def test_html_factory_path(self):
        import arcus_html  # type: ignore
        with self.assertRaises(arcus_html.ArcusHtmlError) as cm:
            arcus_html.extract_html_url(url=self.base + "/page")
        self.assertIn(NO_BROWSER, str(cm.exception))

    def test_html_deep_path_is_an_adapter_error_with_the_reason(self):
        import arcus_html  # type: ignore
        with self.assertRaises(arcus_html.ArcusHtmlError) as cm:
            arcus_html.extract_html_url(url=self.base + "/page", deep=True)
        self.assertIn(NO_BROWSER, str(cm.exception))

    def test_tweet_deep_path(self):
        import arcus_html  # type: ignore
        from arcus.provider_runtime.providers.html import _athena_fetch_page as fp  # type: ignore
        with mock.patch.object(fp, "_is_x_url", lambda u: True):
            with self.assertRaises(arcus_html.ArcusHtmlError) as cm:
                arcus_html.extract_html_url(url=self.base + "/page", deep=True)
        self.assertIn(NO_BROWSER, str(cm.exception))

    def test_video_path(self):
        import arcus_video  # type: ignore
        from arcus.provider_runtime.providers.youtube import youtube as yt  # type: ignore
        with mock.patch.object(yt, "parse_youtube_url", lambda raw: "abcdefghijk"):
            with self.assertRaises(arcus_video.ArcusVideoError) as cm:
                arcus_video.ingest_youtube_url(vault=self.tmp, url=self.base + "/notfound")
        msg = str(cm.exception)
        self.assertRegex(msg, r"exit \d+\) for \S+: \S", "arcus's error follows the exit code")

    def test_file_path(self):
        import arcus_file  # type: ignore
        bad = self.tmp / "broken.pdf"
        bad.write_bytes(b"%PDF-1.4\nnot really a pdf\n")
        with self.assertRaises(arcus_file.ArcusFileError) as cm:
            arcus_file._extract_via_arcus(bad, "pdf")
        self.assertRegex(str(cm.exception), r"exit \d+\) for .+: \S")


class FailureReasonFromStub(unittest.TestCase):
    def test_reads_the_stub_error_first_line_home_shortened(self):
        import arcus_errors  # type: ignore
        d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        home = os.path.expanduser("~")
        (d / "x.json").write_text(
            '{"status": "failed", "error": "fetch_page raised: Executable doesn\'t exist at '
            + home + '/Library/x\\n\\u2554\\u2550\\u2550 box"}')
        self.assertEqual(arcus_errors.failure_reason(d),
                         "fetch_page raised: Executable doesn't exist at ~/Library/x")

    def test_no_stub_is_empty(self):
        import arcus_errors  # type: ignore
        d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        self.assertEqual(arcus_errors.failure_reason(d), "")


# ── 2-4. capture.py: one WARN, the tag, the reason ───────────────────────────
def _warns(out) -> list[str]:
    """The fallback WARN lines (not the older WARNING:THIN_CONTENT marker)."""
    return [ln for ln in out.getvalue().splitlines() if "WARN: arcus failed" in ln]


def _proc(rc=1, stdout="", stderr=ARCUS_STDERR):
    return subprocess.CompletedProcess(args=[], returncode=rc, stdout=stdout, stderr=stderr)


class CaptureHelpers(unittest.TestCase):
    def test_reason_is_the_adapters_own_line(self):
        r = CAP._arcus_reason(_proc())
        self.assertIn(NO_BROWSER, r)
        self.assertNotIn("Traceback", r)

    def test_reason_without_an_adapter_line(self):
        self.assertIn("exited 1", CAP._arcus_reason(_proc(stderr="")))
        self.assertIn("12 chars", CAP._arcus_reason(_proc(rc=0, stdout="x" * 12, stderr="")))

    def test_one_warn_line_and_the_tag(self):
        state = {}
        out = io.StringIO()
        with redirect_stdout(out):
            CAP._note_fallback(state, "the oembed text", "fetch_page raised: boom")
        warn = _warns(out)
        self.assertEqual(len(warn), 1, out.getvalue())
        self.assertIn("fetch_page raised: boom", warn[0])
        self.assertIn("the oembed text", warn[0])
        self.assertEqual(CAP._fallback_extras(state),
                         ["extracted_via_fallback=the oembed text: fetch_page raised: boom"])

    def test_no_fallback_no_tag(self):
        self.assertEqual(CAP._fallback_extras({}), [])

    def test_no_silent_arcus_fallback_print_is_left(self):
        # Every arcus call site (tweet x2, LinkedIn, webpage, video, paper)
        # goes through _note_fallback; the old bare prints were the silence.
        src = (LIB / "capture.py").read_text()
        for old in ("arcus returned no content", "arcus returned insufficient content",
                    "arcus failed; oembed text retained", "Could not extract content (private post",
                    "arcus extraction failed, falling back"):
            self.assertNotIn(old, src, old)
        self.assertGreaterEqual(src.count("_note_fallback(state,"), 7)

    def test_every_raw_writer_carries_the_tag(self):
        src = (LIB / "capture.py").read_text()
        calls = [m.start() for m in re.finditer(r"raw_file = wiki_schema_write\(", src)]
        self.assertGreaterEqual(len(calls), 4)
        for i in calls:
            self.assertIn("_fallback_extras(state)", src[i:i + 600],
                          f"raw writer at offset {i} drops the fallback tag")


class TweetFallsBackToOembed(unittest.TestCase):
    URL = "https://x.com/someone/status/1234567890123456789"

    def test_warns_and_tags(self):
        state = {}
        oembed = '{"html": "<p>short oembed text of the post</p>&mdash; someone"}'
        out = io.StringIO()
        with ExitStack() as st:
            st.enter_context(mock.patch.object(CAP, "_helper", lambda *a, **k: _proc()))
            st.enter_context(mock.patch.object(CAP, "_curl", lambda *a, **k: oembed))
            import fetch_tweet  # type: ignore
            st.enter_context(mock.patch.object(fetch_tweet, "fetch_tweet_json", lambda *a, **k: None))
            st.enter_context(redirect_stdout(out))
            CAP.resolve_tweet(self.URL, self.URL, state)
        warn = _warns(out)
        self.assertEqual(len(warn), 1, out.getvalue())
        self.assertIn(NO_BROWSER, warn[0])
        tag = CAP._fallback_extras(state)
        self.assertTrue(tag and "oembed" in tag[0] and NO_BROWSER in tag[0], tag)


class WebpageFallsBackToPlaceholder(unittest.TestCase):
    def test_raw_is_tagged(self):
        tmp = Path(tempfile.mkdtemp(prefix="athena-fb-web-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        seen = {}

        def fake_write(**kw):
            seen.update(kw)
            return str(tmp / "raw.md")

        state = {}
        out = io.StringIO()
        with ExitStack() as st:
            st.enter_context(mock.patch.object(CAP, "_helper", lambda *a, **k: _proc()))
            st.enter_context(mock.patch.object(CAP, "_curl_title", lambda u: "A Page"))
            st.enter_context(mock.patch.object(CAP, "wiki_schema_write", fake_write))
            st.enter_context(mock.patch.object(CAP, "resolve_raw_path", lambda f, u: str(tmp / "nope.md")))
            st.enter_context(mock.patch.dict(os.environ, {"ATHENA_SKIP_ASSETS": "1"}))
            st.enter_context(redirect_stdout(out))
            CAP.handle_webpage("https://example.org/a", "example-org-a", "2026-11-22",
                               {"webpages": str(tmp)}, state)
        self.assertTrue(any(e.startswith("extracted_via_fallback=") and NO_BROWSER in e
                            for e in seen.get("extras", [])), seen.get("extras"))
        self.assertEqual(len(_warns(out)), 1, out.getvalue())


class PaperFallsBackToAbstract(unittest.TestCase):
    def test_pdf_fulltext_failure_is_recorded(self):
        state = {}
        out = io.StringIO()
        with redirect_stdout(out):
            text = CAP._extract_pdf_fulltext("/no/such/file.pdf", state)
        self.assertEqual(text, "")
        tag = CAP._fallback_extras(state)
        self.assertTrue(tag and "abstract" in tag[0], tag)


_PAD = ("\n\nPadding paragraph to clear the thin-orphan threshold so this raw survives "
        "the lint pass and reaches the checks under test. " * 6)


class LintListsFallbackRaws(unittest.TestCase):
    """Through the real raw writer and the real `kb lint`."""

    def _vault(self, extras):
        d = Path(tempfile.mkdtemp(prefix="athena-fb-lint-"))
        self.addCleanup(shutil.rmtree, d, True)
        (d / "CLAUDE.md").write_text("# vault")
        (d / "RULES.md").write_text("# RULES\n")
        for sub in ("wiki/format/webpages", "inbox", "raw/webpages/artifacts"):
            (d / sub).mkdir(parents=True, exist_ok=True)
        shutil.copytree(ROOT / "bin", d / "bin", ignore=shutil.ignore_patterns("__pycache__"))
        body = d / "body.md"
        body.write_text("## Content\n\nshort oembed text" + _PAD)
        cmd = [sys.executable, str(d / "bin/lib/wiki_schema.py"), "write", "--vault", str(d),
               "--source-type", "webpage", "--url", "https://example.org/fb",
               "--title", "FB", "--input", str(body)]
        for e in extras:
            cmd += ["--extra", e]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        body.unlink()
        return d

    def _lint(self, d):
        return subprocess.run([sys.executable, str(d / "bin" / "kb"), "lint"], cwd=d,
                              capture_output=True, text=True, timeout=120,
                              env=dict(os.environ, KB_ROOT=str(d))).stdout

    def test_tagged_raw_is_listed_with_its_reason(self):
        tag = CAP._fallback_extras({"fallbacks": [
            "the oembed text: fetch_page raised: Executable doesn't exist at ~/x"]})
        out = self._lint(self._vault(tag))
        self.assertIn("Captures made through a fallback", out)
        self.assertIn("Executable doesn't exist", out)

    def test_untagged_raw_is_not_listed_and_not_counted(self):
        out = self._lint(self._vault([]))
        self.assertNotIn("Captures made through a fallback", out)
        self.assertNotIn("fallback", out.lower())


if __name__ == "__main__":
    unittest.main()
