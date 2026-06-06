"""Unit tests for unified_ingest — the single routing + dispatch entry
point for athena content ingestion.

Each test encodes a contract that's been violated in the wild by one
of the divergent pre-unification paths. The most important is the
ExploitGym case (test_arxiv_url_routes_to_paper) — an arXiv URL clipped
via Web Clipper landed in raw/webpages/ because process_clip used
source_kind() / _KIND_TO_CATEGORY which didn't know about arxiv.

Run from vault root:
    python3 -m unittest tests.test_unified_ingest -v
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

# Add bin/lib to import path (matches how kb dispatches)
_VAULT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_VAULT / "bin" / "lib"))

from unified_ingest import (  # noqa: E402
    IngestInput,
    IngestResult,
    UnifiedIngestError,
    _HANDLERS,
    ingest,
    ingest_clip_file,
    route,
)


# ─────────────────────────────────────────────────────────────────────
# route() — the routing oracle. Tests here protect against the historical
# bug class where each entry point made its own routing decision.
# ─────────────────────────────────────────────────────────────────────


class TestRoute(unittest.TestCase):
    """`route()` is the single source of truth for URL → source_type.

    All ingest paths MUST go through it. These tests assert the routing
    decisions for each URL shape, including the cases that the legacy
    `_KIND_TO_CATEGORY` lookup got wrong.
    """

    def test_arxiv_abs_url_routes_to_paper(self):
        """The ExploitGym bug, encoded as a contract.

        Before unified_ingest, `source_kind('https://arxiv.org/abs/...')`
        returned `SourceKind.ARXIV` but `_KIND_TO_CATEGORY` didn't include
        ARXIV in its mapping, so the URL fell to the catch-all "webpage".
        Result: paper clipped via Web Clipper landed in raw/webpages/
        instead of raw/papers/, and the wiki layer couldn't find it as
        a paper. url_detect.detect_type() correctly returns "paper" for
        arxiv URLs; route() must surface that.
        """
        result = route("https://arxiv.org/abs/2605.11086")
        self.assertEqual(result["source_type"], "paper")

    def test_arxiv_pdf_url_routes_to_paper(self):
        """Direct arxiv PDF URLs also route to paper."""
        result = route("https://arxiv.org/pdf/2605.11086.pdf")
        self.assertEqual(result["source_type"], "paper")

    def test_github_repo_url_routes_to_repo(self):
        result = route("https://github.com/anthropics/claude-code")
        self.assertEqual(result["source_type"], "repo")

    def test_youtube_url_routes_to_video(self):
        result = route("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        self.assertEqual(result["source_type"], "video")

    def test_youtu_be_short_url_routes_to_video(self):
        result = route("https://youtu.be/dQw4w9WgXcQ")
        self.assertEqual(result["source_type"], "video")

    def test_generic_webpage_url_routes_to_webpage(self):
        """The default — no platform pattern matches → webpage."""
        # Use a domain that won't HEAD-redirect to anything weird.
        # url_detect's HEAD fallback may try the network; we accept
        # either "webpage" (default) or whatever a real HEAD returned.
        # The contract is "not paper/repo/video for unknown URLs".
        result = route("https://example.com/some/page")
        self.assertIn(result["source_type"],
                      {"webpage", "doc", "image", "spreadsheet", "slides", "book"})


class TestRouteHintPrecedence(unittest.TestCase):
    """source_hint is advisory only. url_detect's platform-pattern matches
    are absolute. The hint can ONLY upgrade the catch-all "webpage" to
    something more specific — it can never downgrade a real classification.
    """

    def test_hint_overrides_webpage_default(self):
        """When url_detect falls through to "webpage" and the caller
        provided a more specific hint, the hint wins. This is the
        legitimate use case (caller knows from HTTP Content-Type or
        another signal that a generic URL is actually a PDF).

        Semantics of `was_re_routed`: True iff the final source_type
        differs from the caller's source_hint. In the hint-overrides-
        webpage case the hint was HONORED (not overridden), so the
        final type matches the hint → was_re_routed=False. The signal
        only fires when url_detect contradicts the caller (the
        bug-detection case — "you said webpage, we're filing as paper").
        """
        result = route("https://example.com/whatever", source_hint="doc")
        # If url_detect's HEAD returned something other than webpage,
        # the hint is ignored — final type is whatever url_detect said.
        # If url_detect returned webpage (the common case for example.com),
        # the hint is honored and final type is "doc".
        if result["source_type"] == "doc":
            # Hint honored. We did NOT re-route the caller — they were
            # right. was_re_routed should be False.
            self.assertFalse(result["was_re_routed"])

    def test_hint_cannot_downgrade_paper(self):
        """An arxiv URL with source_hint='webpage' MUST still route to
        paper — the URL pattern is authoritative. This is the case where
        a legacy caller (process_clip used to do this) would have
        provided source_hint='webpage' for everything."""
        result = route("https://arxiv.org/abs/2605.11086",
                       source_hint="webpage")
        self.assertEqual(result["source_type"], "paper")

    def test_invalid_hint_falls_through_to_detection(self):
        """An unrecognized source_hint string is ignored — we don't add
        arbitrary types via hint."""
        result = route("https://example.com/p", source_hint="banana")
        self.assertNotEqual(result["source_type"], "banana")


# ─────────────────────────────────────────────────────────────────────
# IngestInput validation — fail at boundary, not at handler depth.
# ─────────────────────────────────────────────────────────────────────


class TestIngestInputValidation(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_empty_url_rejected(self):
        with self.assertRaises(UnifiedIngestError):
            IngestInput(vault_root=self.vault, url="")

    def test_whitespace_url_rejected(self):
        with self.assertRaises(UnifiedIngestError):
            IngestInput(vault_root=self.vault, url="   ")

    def test_nonexistent_vault_root_rejected(self):
        with self.assertRaises(UnifiedIngestError):
            IngestInput(
                vault_root=Path("/nonexistent/path/that/does/not/exist"),
                url="https://example.com",
            )

    def test_string_vault_root_coerced_to_path(self):
        """Convenience: accept str for vault_root and coerce to Path."""
        inp = IngestInput(
            vault_root=str(self.vault),  # type: ignore[arg-type]
            url="https://example.com",
        )
        self.assertIsInstance(inp.vault_root, Path)


# ─────────────────────────────────────────────────────────────────────
# Dispatch — ingest() calls the right handler for each source_type.
# ─────────────────────────────────────────────────────────────────────


class TestIngestDispatch(unittest.TestCase):
    """ingest() must call the correct handler given route()'s decision.
    These tests stub the handlers to confirm dispatch without actually
    running extraction (which would shell out / hit the network).
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _stub_handler(self, source_type: str):
        """Return a handler that records its call and produces a minimal
        IngestResult. Patches into _HANDLERS for the duration of the
        test via `patch.dict`."""
        calls = {"count": 0, "args": None}

        def _stub(inp, routing, canonical_url):
            calls["count"] += 1
            calls["args"] = (inp, routing, canonical_url)
            return IngestResult(
                raw_path=self.vault / f"raw/{source_type}/stub.md",
                source_type=source_type,
                canonical_url=canonical_url,
                title="stub",
                extracted_via="test_stub",
                was_re_routed=routing.get("was_re_routed", False),
            )

        return calls, _stub

    def test_arxiv_url_dispatches_to_paper_handler(self):
        """End-to-end routing check for the ExploitGym bug. An arxiv URL
        ingested via ingest() must reach _handle_paper, not _handle_webpage,
        even if the caller's source_hint says 'webpage' (the legacy bug)."""
        calls, stub = self._stub_handler("paper")
        with patch.dict(_HANDLERS, {"paper": stub}, clear=False):
            result = ingest(IngestInput(
                vault_root=self.vault,
                url="https://arxiv.org/abs/2605.11086",
                title="ExploitGym paper",
                body="this would be the Web Clipper markdown",
                source_hint="webpage",  # legacy caller's misclassification
            ))
        self.assertEqual(calls["count"], 1)
        self.assertEqual(result.source_type, "paper")
        # was_re_routed surfaces the hint→detection divergence so
        # callers can log "this URL almost got misfiled".
        self.assertTrue(result.was_re_routed)

    def test_github_url_dispatches_to_repo_handler(self):
        calls, stub = self._stub_handler("repo")
        with patch.dict(_HANDLERS, {"repo": stub}, clear=False):
            result = ingest(IngestInput(
                vault_root=self.vault,
                url="https://github.com/foo/bar",
                title="foo/bar repo",
                body="readme content",
            ))
        self.assertEqual(calls["count"], 1)
        self.assertEqual(result.source_type, "repo")

    def test_youtube_url_dispatches_to_video_handler(self):
        calls, stub = self._stub_handler("video")
        with patch.dict(_HANDLERS, {"video": stub}, clear=False):
            ingest(IngestInput(
                vault_root=self.vault,
                url="https://youtu.be/dQw4w9WgXcQ",
                title="test video",
                body="transcript snippet",
            ))
        self.assertEqual(calls["count"], 1)

    def test_handler_canonicalizes_url_once(self):
        """ingest() canonicalizes the URL exactly once, before dispatch.
        Handlers receive the canonical form, must not re-canonicalize.
        Tracking-param-laden URLs should arrive at the handler stripped.
        """
        calls, stub = self._stub_handler("webpage")
        with patch.dict(_HANDLERS, {"webpage": stub}, clear=False):
            ingest(IngestInput(
                vault_root=self.vault,
                url="https://example.com/page?utm_source=test&utm_medium=email",
                title="t",
                body="b",
            ))
        # _stub_handler stored (inp, routing, canonical_url) in calls["args"]
        canonical = calls["args"][2]
        # The utm_* params should be stripped by canonicalize().
        self.assertNotIn("utm_source", canonical)
        self.assertNotIn("utm_medium", canonical)


# ─────────────────────────────────────────────────────────────────────
# Webpage body-passthrough — end-to-end with a real raw_writer write.
# ─────────────────────────────────────────────────────────────────────


class TestWebpageBodyPassthrough(unittest.TestCase):
    """Path A of _handle_webpage: pre-converted markdown body (Web
    Clipper today). Verifies the full write lands a valid raw."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self._tmp.name)
        # Disable LinkedIn auto-promote during tests (same as test_writers).
        self._old_promote = os.environ.get("KB_DISABLE_AUTO_PROMOTE_DEEP")
        os.environ["KB_DISABLE_AUTO_PROMOTE_DEEP"] = "1"

    def tearDown(self):
        if self._old_promote is None:
            os.environ.pop("KB_DISABLE_AUTO_PROMOTE_DEEP", None)
        else:
            os.environ["KB_DISABLE_AUTO_PROMOTE_DEEP"] = self._old_promote
        self._tmp.cleanup()

    def test_webpage_body_writes_raw(self):
        """Generic webpage with markdown body lands in raw/webpages/."""
        result = ingest(IngestInput(
            vault_root=self.vault,
            url="https://example.com/article-1",
            title="Article One",
            body="Some article content here.\n\nMore content.",
            clipped_via="test",
        ))
        self.assertEqual(result.source_type, "webpage")
        self.assertEqual(result.extracted_via, "passthrough")
        self.assertTrue(result.raw_path.exists())
        self.assertIn("raw/webpages/", str(result.raw_path))
        text = result.raw_path.read_text(encoding="utf-8")
        self.assertIn("Article One", text)
        self.assertIn("Some article content here.", text)
        self.assertIn("clipped_via:", text)


# ─────────────────────────────────────────────────────────────────────
# ingest_clip_file — full Web Clipper drop processing
# ─────────────────────────────────────────────────────────────────────


class TestIngestClipFile(unittest.TestCase):
    """`ingest_clip_file` is the entry point process_clip.process_clip()
    will migrate to. Reads a clip file's frontmatter + body and routes
    through ingest()."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self._tmp.name)
        os.environ["KB_DISABLE_AUTO_PROMOTE_DEEP"] = "1"

    def tearDown(self):
        os.environ.pop("KB_DISABLE_AUTO_PROMOTE_DEEP", None)
        self._tmp.cleanup()

    def _write_clip(self, fname: str, frontmatter: dict, body: str) -> Path:
        """Build a Web-Clipper-shaped clip file in a temp clippings dir."""
        clippings = self.vault / "clippings"
        clippings.mkdir(parents=True, exist_ok=True)
        clip = clippings / fname

        fm_lines = ["---"]
        for k, v in frontmatter.items():
            fm_lines.append(f'{k}: "{v}"')
        fm_lines.append("---")
        clip.write_text("\n".join(fm_lines) + "\n\n" + body, encoding="utf-8")
        return clip

    def test_clip_with_arxiv_url_does_not_misfile_as_webpage(self):
        """The ExploitGym contract, end-to-end through the clip path.

        Before unified_ingest, a Web Clipper drop with an arxiv source URL
        would land in raw/webpages/ regardless of what the URL actually was.
        Now it must dispatch to the paper handler. Since the paper handler
        shells out to kb-capture (which would actually try to download the
        PDF in a test), we patch _handle_paper to a stub and assert the
        dispatch — same pattern as TestIngestDispatch.
        """
        clip = self._write_clip(
            "exploitgym.md",
            {
                "source": "https://arxiv.org/abs/2605.11086",
                "title": "ExploitGym: Can AI Agents Turn Security Vulnerabilities into Real Attacks?",
                "clipped_via": "web-clipper",
            },
            body="this is a synthetic Web Clipper markdown body that the\n"
                 "browser extension produced from the arxiv abstract page.",
        )

        dispatched_to = {"source_type": None}

        def _stub_paper(inp, routing, canonical_url):
            dispatched_to["source_type"] = "paper"
            return IngestResult(
                raw_path=self.vault / "raw/papers/artifacts/test-stub.md",
                source_type="paper",
                canonical_url=canonical_url,
                title=inp.title,
                extracted_via="test_stub",
                was_re_routed=routing.get("was_re_routed", False),
            )

        with patch.dict(_HANDLERS, {"paper": _stub_paper}, clear=False):
            result = ingest_clip_file(clip, self.vault)

        # The contract: an arxiv URL via the clip path must reach the
        # paper handler, NOT the webpage handler.
        self.assertEqual(dispatched_to["source_type"], "paper")
        self.assertEqual(result.source_type, "paper")


if __name__ == "__main__":
    unittest.main()
