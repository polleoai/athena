"""kb-capture health_check Content-Type routing — issue #138.

When a 'webpage' URL actually serves binary bytes (image/PDF/octet-stream),
kb-capture must reject it up front rather than writing the binary body into a
.md raw as text. The guard lives in health_check (bin/kb-capture) and is a pure
function of the `curl -sI` HEAD output, so we test it by mocking `_curl` — no
network. This guard previously had zero coverage.

Run from vault root:
    python3 -m pytest tests/test_kb_capture_content_type.py -v
"""

from __future__ import annotations

import importlib.util
import sys
import unittest
from importlib.machinery import SourceFileLoader
from pathlib import Path
from unittest import mock

_VAULT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_VAULT / "bin" / "lib"))


def _load_kb_capture():
    path = _VAULT / "bin" / "lib" / "capture.py"
    loader = SourceFileLoader("kb_capture_mod", str(path))
    spec = importlib.util.spec_from_loader("kb_capture_mod", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


_KBC = _load_kb_capture()


def _head(status: str, content_type: str | None) -> str:
    lines = [f"HTTP/2 {status} "]
    if content_type is not None:
        lines.append(f"Content-Type: {content_type}")
    return "\r\n".join(lines) + "\r\n"


class _Patched(unittest.TestCase):
    def setUp(self):
        # die() calls _cleanup() then sys.exit(1); neutralize cleanup so the test
        # only observes the SystemExit, not tmp-file side effects.
        self._p_cleanup = mock.patch.object(_KBC, "_cleanup", lambda: None)
        self._p_cleanup.start()
        self.addCleanup(self._p_cleanup.stop)

    def _curl_returns(self, head: str):
        return mock.patch.object(_KBC, "_curl", lambda *a, **k: head)


class TestWebpageContentTypeGuard(_Patched):
    def test_html_passes(self):
        with self._curl_returns(_head("200", "text/html; charset=utf-8")):
            self.assertIsNone(_KBC.health_check("https://example.com/p", "webpage"))

    def test_json_xml_rss_pass(self):
        for ct in ("application/json", "application/xml",
                   "application/rss+xml", "application/atom+xml",
                   "application/xhtml+xml"):
            with self._curl_returns(_head("200", ct)):
                self.assertIsNone(
                    _KBC.health_check("https://example.com/feed", "webpage"),
                    f"{ct} should pass the webpage guard",
                )

    def test_image_rejected(self):
        with self._curl_returns(_head("200", "image/jpeg")):
            with self.assertRaises(SystemExit):
                _KBC.health_check("https://pbs.twimg.com/media/x?format=jpg", "webpage")

    def test_pdf_rejected(self):
        with self._curl_returns(_head("200", "application/pdf")):
            with self.assertRaises(SystemExit):
                _KBC.health_check("https://example.com/paper.pdf", "webpage")

    def test_octet_stream_rejected(self):
        with self._curl_returns(_head("200", "application/octet-stream")):
            with self.assertRaises(SystemExit):
                _KBC.health_check("https://example.com/file.bin", "webpage")

    def test_unrecognized_content_type_warns_but_proceeds(self):
        """Unknown types are a warning, not a hard reject (return None)."""
        with self._curl_returns(_head("200", "application/vnd.custom-thing")):
            self.assertIsNone(_KBC.health_check("https://example.com/x", "webpage"))


class TestStatusGuard(_Patched):
    def test_http_404_is_dead(self):
        with self._curl_returns(_head("404", "text/html")):
            with self.assertRaises(SystemExit):
                _KBC.health_check("https://example.com/missing", "webpage")

    def test_connection_failure_is_dead(self):
        with self._curl_returns(""):  # no HEAD output → status stays 000
            with self.assertRaises(SystemExit):
                _KBC.health_check("https://nope.invalid/x", "webpage")


class TestSocialSkip(_Patched):
    def test_social_url_skips_health_check_entirely(self):
        """x.com / linkedin URLs short-circuit before any network call."""
        called = {"curl": False}

        def _boom(*a, **k):
            called["curl"] = True
            return ""

        with mock.patch.object(_KBC, "_curl", _boom):
            self.assertIsNone(_KBC.health_check("https://x.com/u/status/1", "webpage"))
        self.assertFalse(called["curl"], "social URL must not trigger a HEAD request")


if __name__ == "__main__":
    unittest.main()
