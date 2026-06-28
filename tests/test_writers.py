"""Unit tests for raw_writer + wiki_writer.

Each test corresponds to a bug shape that one of the existing lint
sections (#38, #39, #41, #42, #46, #47, #48, #49) catches. Once the
writers are the only path into raw + wiki (Phase 4), the matching lint
sections become unnecessary — these tests are the permanent contract
those lints encoded.

Run from vault root:
    python3 -m unittest tests.test_writers -v
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

# Add bin/lib to import path (matches how kb dispatches)
_VAULT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_VAULT / "bin" / "lib"))

from raw_writer import RawWriterError, write_raw  # noqa: E402
from schemas import WikiShape  # noqa: E402
from wiki_writer import WikiWriterError, write_wiki  # noqa: E402


class _VaultCase(unittest.TestCase):
    """Base class that provisions a temporary vault for each test."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.vault = self._tmp.name
        # Disable the LinkedIn → capture-deep auto-promote during tests.
        # The auth marker file lives in $HOME, not the test vault, so a
        # developer with a real Playwright session would otherwise see
        # tests try to invoke `bin/kb capture-deep` against linkedin.com
        # for real (network call from unit tests). The env var is the
        # documented test escape hatch in process_clip.py.
        import os as _os_test
        self._prev_promote_disable = _os_test.environ.get("ATHENA_DISABLE_PLAYWRIGHT_PROMOTE")
        _os_test.environ["ATHENA_DISABLE_PLAYWRIGHT_PROMOTE"] = "1"

    def tearDown(self):
        self._tmp.cleanup()
        # Restore prior env state so test pollution doesn't leak.
        import os as _os_test
        if self._prev_promote_disable is None:
            _os_test.environ.pop("ATHENA_DISABLE_PLAYWRIGHT_PROMOTE", None)
        else:
            _os_test.environ["ATHENA_DISABLE_PLAYWRIGHT_PROMOTE"] = self._prev_promote_disable


# ─── raw_writer ──────────────────────────────────────────────────────


class TestRawWriter(_VaultCase):

    def test_lands_under_artifacts(self):
        """Bug class lint #49 catches: raw outside artifacts/.

        write_raw constructs the path; it can't drift."""
        path = write_raw(
            vault_root=self.vault,
            source_type="repo",
            url="https://github.com/anthropics/skills",
            title="anthropics/skills",
            body="content",
        )
        # Must be inside raw/<cat>/artifacts/
        rel = Path(path).relative_to(self.vault)
        self.assertEqual(rel.parts[0], "raw")
        self.assertEqual(rel.parts[1], "repos")
        self.assertEqual(rel.parts[2], "artifacts")

    def test_url_canonicalized_at_write(self):
        """Bug class lint #38 catches: tracking-param dups.

        Two captures of same X tweet with different ?s=12&t=... should
        produce identical raws (same canonical URL → same slug → same
        destination → second write hits collision and overwrites no data
        we didn't already have)."""
        common_kwargs = dict(
            vault_root=self.vault,
            source_type="webpage",
            title="Some Tweet",
            body="body content",
        )
        p1 = write_raw(
            url="https://x.com/regent0x_/status/2049499354323399002?s=12&t=ABC",
            **common_kwargs,
        )
        # Second capture, different tracking params, same canonical URL
        p2 = write_raw(
            url="https://x.com/regent0x_/status/2049499354323399002?utm_source=foo",
            **common_kwargs,
        )
        # Both should write to the SAME path (same canonical URL → same slug)
        self.assertEqual(p1, p2)

    def test_collision_bait_title_rejected(self):
        """Bug class lint #39 catches: 'post-linkedin', 'untitled' slugs.

        write_raw refuses to derive a slug from collision-bait titles
        when category requires URL_DERIVED."""
        with self.assertRaises(RawWriterError) as ctx:
            write_raw(
                vault_root=self.vault,
                source_type="webpage",
                url=None,  # forces title-based slug attempt
                title="Untitled",
                body="content",
            )
        self.assertIn("URL", str(ctx.exception))

    def test_pdf_host_transform_at_write(self):
        """HuggingFace /blob/ → /resolve/ — was the DeepSeek wrapper-page
        ingestion bug. The writer applies it before storing the URL."""
        path = write_raw(
            vault_root=self.vault,
            source_type="paper",
            url="https://huggingface.co/foo/bar/blob/main/paper.pdf",
            title="Paper",
            body="body",
        )
        text = Path(path).read_text()
        self.assertIn("/resolve/main/paper.pdf", text)
        self.assertNotIn("/blob/main/paper.pdf", text)

    def test_leaked_frontmatter_stripped(self):
        """Bug class lint #48 catches: raw FM leaked into wiki body, but
        the same shape can leak from web-clipped content INTO the raw
        body. The writer strips it at the boundary."""
        path = write_raw(
            vault_root=self.vault,
            source_type="webpage",
            url="https://example.com/article",
            title="Article",
            body=(
                "---\n"
                'title: "leaked"\n'
                "foo: bar\n"
                "---\n\n"
                "# Real content\n\nbody text"
            ),
        )
        text = Path(path).read_text()
        # The writer's own frontmatter + the body's --- between sections.
        # There should be exactly ONE frontmatter block (between the first
        # two ---), not a second one inside the body.
        self.assertEqual(text.count("---"), 2)
        self.assertIn("Real content", text)
        self.assertNotIn('title: "leaked"', text)

    def test_empty_title_rejected(self):
        """Bug class lint #41 catches: missing/empty fields."""
        with self.assertRaises(RawWriterError):
            write_raw(
                vault_root=self.vault,
                source_type="repo",
                url="https://github.com/foo/bar",
                title="",
                body="content",
            )

    def test_book_uses_title_derived_slug(self):
        """Books have no URL — TITLE_DERIVED policy."""
        path = write_raw(
            vault_root=self.vault,
            source_type="book",
            url=None,
            title="Introduction to Measure Theory",
            body="content",
        )
        self.assertIn("introduction-to-measure-theory", str(path))

    def test_thin_overwrite_rejected(self):
        """0.10.0: refuse to overwrite an existing larger raw with thin
        content (auth-wall captures, failed fetches). This protects
        against the data-loss class witnessed during capture-deep
        development when an auth-wall clip clobbered the good Praneeta
        raw. Without git history, the data would have been lost."""
        # Write a substantial raw first
        write_raw(
            vault_root=self.vault,
            source_type="webpage",
            url="https://www.linkedin.com/posts/somebody_test-7458999",
            title="Real Post Title",
            body="This is a substantial post body with real content that would be valuable to preserve. " * 30,
        )
        # Now try to overwrite with thin auth-wall-style content
        with self.assertRaises(RawWriterError) as ctx:
            write_raw(
                vault_root=self.vault,
                source_type="webpage",
                url="https://www.linkedin.com/posts/somebody_test-7458999",
                title="Sign Up | LinkedIn",
                body="Join LinkedIn now",
            )
        self.assertIn("refusing to overwrite", str(ctx.exception))

    def test_thin_first_write_allowed(self):
        """If no existing raw at the target path, thin content writes
        normally — the guard is overwrite-only, not write-only."""
        path = write_raw(
            vault_root=self.vault,
            source_type="webpage",
            url="https://example.com/foo",
            title="Tiny page",
            body="Tiny body.",
        )
        self.assertTrue(path.exists())

    def test_overwrite_with_post_not_found_marker_rejected(self):
        """0.10.13: 'Post not found' / 'This post was deleted' content must
        NEVER overwrite an existing raw, regardless of size ratio. The Kumo
        case showed the size guard alone wasn't enough — the deleted-post
        page (641 bytes) was only 2.4x smaller than the real raw (1548
        bytes), slipping past the old 5x rule. Marker-based detection
        catches this regardless of size."""
        url = "https://linkedin.com/posts/ugcpost-1234567890"
        # Plant a real raw — sized to NOT trigger the size guard
        # (>1024 bytes so the size branch wouldn't fire even if asked).
        body_real = ("Real post body — substantive content. " * 40)  # ~1.5KB
        write_raw(vault_root=self.vault, source_type="webpage", url=url,
                  title="Introducing Kumo", body=body_real)
        # Try to overwrite with a "Post not found" page — note this
        # would NOT trigger the size guard (it's > 1024 bytes too).
        body_404 = ("Post not found. This post was deleted or removed. " * 30)
        with self.assertRaises(RawWriterError) as ctx:
            write_raw(vault_root=self.vault, source_type="webpage", url=url,
                      title="Post | LinkedIn", body=body_404)
        self.assertIn("degraded-fetch", str(ctx.exception))

    def test_linkedin_interstitial_rejected_on_first_write(self):
        """0.10.16: LinkedIn external-link interstitial pages must be
        refused at the writer layer, regardless of overwrite vs new write.
        These pages have a fixed shape (LinkedIn safety warning, no real
        content) and produce sparse wiki pages with garbage titles when
        landed. Bug class surfaced when sparse Discord/decodingtrust/arxiv
        pages appeared in Recently Added with markdown-link literal titles."""
        body = (
            "# LinkedIn\n\n"
            "# This link will take you to a page that's not on LinkedIn\n\n"
            "## Because this is an external link, we're unable to verify it for safety.\n\n"
            "[https://discord.gg/V4fG6NcVc](https://discord.gg/V4fG6NcVc)\n"
        )
        with self.assertRaises(RawWriterError) as ctx:
            write_raw(
                vault_root=self.vault, source_type="webpage",
                url="https://lnkd.in/gnQ7iAAf",
                title="LinkedIn", body=body,
            )
        self.assertIn("interstitial", str(ctx.exception).lower())
        # Destination URL should be extracted into the error message
        self.assertIn("discord.gg", str(ctx.exception))

    def test_linkedin_interstitial_rejected_even_with_no_dest_url(self):
        """The destination-URL extraction is best-effort; if no markdown
        link appears in the body, the write still gets refused but the
        error message says no destination was recoverable."""
        body = (
            "# LinkedIn\n\nBecause this is an external link, "
            "we're unable to verify it for safety.\n"
        )
        with self.assertRaises(RawWriterError) as ctx:
            write_raw(
                vault_root=self.vault, source_type="webpage",
                url="https://lnkd.in/abc",
                title="LinkedIn", body=body,
            )
        self.assertIn("interstitial", str(ctx.exception).lower())
        self.assertIn("No destination URL", str(ctx.exception))

    def test_overwrite_with_thin_2x_smaller_rejected(self):
        """0.10.13: tightened ratio from 5x → 2x. The Kumo case (1548 →
        641 bytes, 2.4x) slipped past the old rule and clobbered the
        good capture. Test pins the new threshold."""
        url = "https://example.com/test-2x"
        # Write a 1500-byte raw
        body_real = "x" * 1500
        write_raw(vault_root=self.vault, source_type="webpage", url=url,
                  title="Real", body=body_real)
        # Try to overwrite with 600-byte content (2.5x smaller, also < 1024)
        with self.assertRaises(RawWriterError) as ctx:
            write_raw(vault_root=self.vault, source_type="webpage", url=url,
                      title="Tiny", body="y" * 600)
        self.assertIn("refusing to overwrite", str(ctx.exception))

    def test_unknown_source_type_rejected(self):
        with self.assertRaises(RawWriterError):
            write_raw(
                vault_root=self.vault,
                source_type="bogus",
                url="https://example.com",
                title="Foo",
                body="bar",
            )


# ─── wiki_writer ─────────────────────────────────────────────────────


class TestWikiWriter(_VaultCase):

    def test_standard_round_trips(self):
        path = write_wiki(
            vault_root=self.vault,
            shape=WikiShape.STANDARD,
            title="Test Page",
            source_type="webpage",
            summary="A summary.",
            tags=["test"],
            raw_path="raw/webpages/artifacts/test.md",
            url="https://example.com/test",
        )
        text = Path(path).read_text()
        self.assertIn('title: "Test Page"', text)
        self.assertIn('summary: "A summary."', text)
        self.assertIn('url: "https://example.com/test"', text)

    def test_url_canonicalized(self):
        """The wiki url: field is always canonical at write time."""
        path = write_wiki(
            vault_root=self.vault,
            shape=WikiShape.STANDARD,
            title="Tracker",
            source_type="webpage",
            summary="x",
            raw_path="raw/webpages/artifacts/x.md",
            url="https://github.com/anthropics/skills/?utm_source=share",
        )
        text = Path(path).read_text()
        self.assertIn('url: "https://github.com/anthropics/skills"', text)

    def test_raw_path_must_be_under_artifacts(self):
        """Bug class lint #49 catches the analogue on the wiki side: a
        wiki page can't reference a raw_path outside artifacts/."""
        with self.assertRaises(WikiWriterError):
            write_wiki(
                vault_root=self.vault,
                shape=WikiShape.STANDARD,
                title="Bad raw_path",
                source_type="webpage",
                summary="x",
                raw_path="raw/webpages/non-canonical-location.md",  # missing artifacts/
                url="https://example.com",
            )

    def test_merged_requires_two_or_more(self):
        with self.assertRaises(WikiWriterError):
            write_wiki(
                vault_root=self.vault,
                shape=WikiShape.MERGED,
                title="Singleton merge",
                source_type="webpage",
                summary="x",
                raw_paths=["raw/webpages/artifacts/only.md"],
                urls=["https://example.com"],
            )

    def test_merged_requires_lists_same_length(self):
        with self.assertRaises(WikiWriterError):
            write_wiki(
                vault_root=self.vault,
                shape=WikiShape.MERGED,
                title="Mismatched merge",
                source_type="webpage",
                summary="x",
                raw_paths=[
                    "raw/webpages/artifacts/a.md",
                    "raw/webpages/artifacts/b.md",
                ],
                urls=["https://example.com"],  # only one URL
            )

    def test_synthesis_no_raw_path(self):
        """Synthesis pages (entity/topic/insight) have NO raw_path/url.

        The frontmatter must not contain those fields at all."""
        path = write_wiki(
            vault_root=self.vault,
            shape=WikiShape.SYNTHESIS,
            title="A Topic",
            source_type="topic",
            summary="A topic synthesis.",
            tags=["test"],
            related=["[[Some Page]]"],
        )
        text = Path(path).read_text()
        self.assertNotIn("raw_path:", text)
        self.assertNotIn("url:", text)

    def test_redirect_stub_minimal(self):
        path = write_wiki(
            vault_root=self.vault,
            shape=WikiShape.REDIRECT,
            title="Old Name",
            redirect_to="New Name",
            source_type="webpage",
        )
        text = Path(path).read_text()
        self.assertIn("redirect: true", text)
        self.assertIn('redirect_to: "New Name"', text)
        self.assertNotIn("source_type", text)
        self.assertNotIn("summary", text)

    def test_redirect_stub_mtime_is_fresh(self):
        """2026-05-21: Dataview's per-file metadata cache only invalidates
        on content modify events, not atomic-replace (os.replace). Without
        an explicit utime call, a freshly-written stub can keep an old
        mtime that pre-dates the user's Obsidian session — so Dataview
        skips re-indexing and the stub's old-name entry lingers in
        queries that exclude `!redirect`. write_wiki_stub now bumps
        mtime explicitly; this test guards the contract."""
        import time as _t
        from wiki_schema import write_wiki_stub
        before = _t.time()
        out = write_wiki_stub(
            vault=Path(self.vault),
            source_type="webpage",
            old_name="Old Title To Redirect",
            new_name="New Title After Rename",
        )
        # Allow a tiny grace window for FS timestamp resolution
        # (HFS+ has 1s granularity on some macOS configs).
        self.assertGreaterEqual(out.stat().st_mtime, before - 1.0)

    def test_leaked_frontmatter_in_body_stripped(self):
        """Bug class lint #48: the writer strips a leading --- block from
        body to prevent double-frontmatter rendering."""
        path = write_wiki(
            vault_root=self.vault,
            shape=WikiShape.STANDARD,
            title="Leaky",
            source_type="webpage",
            summary="x",
            raw_path="raw/webpages/artifacts/leaky.md",
            url="https://example.com",
            body=(
                "---\n"
                'leaked: "yes"\n'
                "---\n\n"
                "# Body\n\nactual content"
            ),
        )
        text = Path(path).read_text()
        # Exactly one frontmatter block — the writer's own.
        self.assertEqual(text.count("---"), 2)
        self.assertNotIn('leaked: "yes"', text)

    def test_summary_required(self):
        """Bug class lint #41 / #42 partial: missing summary."""
        with self.assertRaises(WikiWriterError):
            write_wiki(
                vault_root=self.vault,
                shape=WikiShape.STANDARD,
                title="No summary",
                source_type="webpage",
                summary="",
                raw_path="raw/webpages/artifacts/x.md",
                url="https://example.com",
            )

    def test_overwrite_protection(self):
        """Two writes to same title without overwrite=True must fail —
        prevents silent data loss."""
        kwargs = dict(
            vault_root=self.vault,
            shape=WikiShape.STANDARD,
            title="Twice",
            source_type="webpage",
            summary="x",
            raw_path="raw/webpages/artifacts/twice.md",
            url="https://example.com",
        )
        write_wiki(**kwargs)
        with self.assertRaises(WikiWriterError):
            write_wiki(**kwargs)
        # With overwrite=True, no error
        write_wiki(overwrite=True, **kwargs)


# ─── Schema discriminator (regression tests for code-review findings) ─


class TestDetectWikiShape(unittest.TestCase):
    """Cover the AmbiguousShapeError cases — mutually exclusive markers."""

    def test_redirect_plus_content_rejected(self):
        from schemas import AmbiguousShapeError, detect_wiki_shape
        with self.assertRaises(AmbiguousShapeError):
            detect_wiki_shape({
                "title": "x", "redirect": True, "redirect_to": "y",
                "source_type": "webpage",  # ← shouldn't be on a redirect
            })

    def test_singular_plus_plural_raw_rejected(self):
        from schemas import AmbiguousShapeError, detect_wiki_shape
        with self.assertRaises(AmbiguousShapeError):
            detect_wiki_shape({
                "title": "x",
                "raw_path": "raw/webpages/artifacts/a.md",
                "raw_paths": [
                    "raw/webpages/artifacts/a.md",
                    "raw/webpages/artifacts/b.md",
                ],
            })

    def test_singular_plus_plural_url_rejected(self):
        from schemas import AmbiguousShapeError, detect_wiki_shape
        with self.assertRaises(AmbiguousShapeError):
            detect_wiki_shape({
                "title": "x",
                "url": "https://a.com",
                "urls": ["https://a.com", "https://b.com"],
            })

    def test_pure_redirect_ok(self):
        from schemas import detect_wiki_shape, WikiShape
        self.assertEqual(
            detect_wiki_shape({"title": "x", "redirect": True, "redirect_to": "y"}),
            WikiShape.REDIRECT,
        )


# ─── process_clip end-to-end ─────────────────────────────────────────


class TestProcessClip(_VaultCase):
    """Web Clipper drop → typed raw artifact. The session's primary symptom
    path. Each case here is a regression guard against the bug class that
    started the schema refactor."""

    def _write_clip(self, name: str, source: str, body: str = "body content"):
        clip_dir = Path(self.vault) / "clippings"
        clip_dir.mkdir(exist_ok=True)
        clip = clip_dir / name
        clip.write_text(
            f'---\ntitle: "Post | LinkedIn"\nsource: "{source}"\n'
            f'created: 2026-05-09\ntags:\n  - "clippings"\n---\n\n'
            f'# Test\n{body}',
            encoding="utf-8",
        )
        return clip

    def test_linkedin_clip_routes_to_webpages(self):
        from process_clip import process_clip
        clip = self._write_clip(
            "linkedin.md",
            "https://www.linkedin.com/posts/somebody_test-7455-tF2k/?utm_source=share",
        )
        path = process_clip(clip, self.vault)
        rel = Path(path).relative_to(self.vault)
        self.assertEqual(rel.parts[:3], ("raw", "webpages", "artifacts"))
        # Slug must be URL-derived (the bug-class lint #39 prevention)
        self.assertIn("linkedin-com-posts-somebody", str(path))
        # URL must be canonical (lint #38 prevention)
        text = Path(path).read_text()
        self.assertIn('source: "https://linkedin.com/posts/somebody_test-7455-tF2k"', text)

    def test_github_clip_routes_to_repos(self):
        """github URL via clip path → unified_ingest dispatches to the
        repo handler. Post-migration the repo handler delegates to
        bin/kb-capture (not present in the test temp-vault), so we stub
        the handler to assert the routing decision without requiring
        the real extractor. The contract this protects: github URLs
        must NOT fall through to the webpage handler (the legacy
        _KIND_TO_CATEGORY bug for paper/repo/video URLs)."""
        from unittest.mock import patch
        from pathlib import Path as _Path
        from process_clip import process_clip
        import unified_ingest

        clip = self._write_clip("gh.md", "https://github.com/foo/bar")

        dispatched = {"to": None}

        def _stub_repo(inp, routing, canonical_url):
            dispatched["to"] = "repo"
            stub_path = _Path(self.vault) / "raw" / "repos" / "artifacts" / "stub.md"
            stub_path.parent.mkdir(parents=True, exist_ok=True)
            stub_path.write_text("stub", encoding="utf-8")
            return unified_ingest.IngestResult(
                raw_path=stub_path,
                source_type="repo",
                canonical_url=canonical_url,
                title=inp.title,
                extracted_via="test_stub",
                was_re_routed=routing.get("was_re_routed", False),
            )

        with patch.dict(unified_ingest._HANDLERS, {"repo": _stub_repo}, clear=False):
            path = process_clip(clip, self.vault)
        self.assertEqual(dispatched["to"], "repo")
        self.assertEqual(_Path(path).relative_to(self.vault).parts[:3],
                         ("raw", "repos", "artifacts"))

    def test_youtube_clip_routes_to_videos(self):
        """youtube URL via clip path → unified_ingest dispatches to the
        video handler. Same pattern as the github test above — assert
        the routing decision without invoking the real kb-capture
        extractor."""
        from unittest.mock import patch
        from pathlib import Path as _Path
        from process_clip import process_clip
        import unified_ingest

        clip = self._write_clip("yt.md", "https://www.youtube.com/watch?v=abc123")

        dispatched = {"to": None}

        def _stub_video(inp, routing, canonical_url):
            dispatched["to"] = "video"
            stub_path = _Path(self.vault) / "raw" / "videos" / "artifacts" / "stub.md"
            stub_path.parent.mkdir(parents=True, exist_ok=True)
            stub_path.write_text("stub", encoding="utf-8")
            return unified_ingest.IngestResult(
                raw_path=stub_path,
                source_type="video",
                canonical_url=canonical_url,
                title=inp.title,
                extracted_via="test_stub",
                was_re_routed=routing.get("was_re_routed", False),
            )

        with patch.dict(unified_ingest._HANDLERS, {"video": _stub_video}, clear=False):
            path = process_clip(clip, self.vault)
        self.assertEqual(dispatched["to"], "video")
        self.assertEqual(_Path(path).relative_to(self.vault).parts[:3],
                         ("raw", "videos", "artifacts"))

    def test_missing_source_url_rejected(self):
        from process_clip import process_clip, ProcessClipError
        clip_dir = Path(self.vault) / "clippings"
        clip_dir.mkdir(exist_ok=True)
        clip = clip_dir / "nosrc.md"
        clip.write_text(
            '---\ntitle: "x"\n---\n\n# x\nbody', encoding="utf-8",
        )
        with self.assertRaises(ProcessClipError) as ctx:
            process_clip(clip, self.vault)
        self.assertIn("source URL", str(ctx.exception))

    def test_empty_body_falls_back_to_network_capture(self):
        # An empty-body clip (iframe-heavy / JS-rendered page) is RECOVERABLE,
        # not fatal: process_clip falls back to a network fetch of the URL
        # instead of hard-failing (which used to leave the clip stuck in the
        # watched inbox, spamming "clip body is empty"). See
        # test_process_clip_empty_body.py for the full contract. Here we just
        # confirm an empty body no longer raises outright. ingest is stubbed
        # so the test stays offline.
        import process_clip as pc
        import unified_ingest as ui
        clip_dir = Path(self.vault) / "clippings"
        clip_dir.mkdir(exist_ok=True)
        clip = clip_dir / "empty.md"
        clip.write_text(
            '---\ntitle: "x"\nsource: "https://example.com"\n---\n',
            encoding="utf-8",
        )
        fake_raw = Path(self.vault) / "raw/webpages/artifacts/example.md"
        fake_raw.parent.mkdir(parents=True, exist_ok=True)
        fake_raw.write_text("ok", encoding="utf-8")
        orig_ingest, orig_touch = ui.ingest, pc._touch_wiki_last_updated_for_url
        pc._touch_wiki_last_updated_for_url = lambda *a, **k: None
        ui.ingest = lambda inp: ui.IngestResult(
            raw_path=fake_raw, source_type="webpage", canonical_url=inp.url,
            title="x", extracted_via="arcus", was_re_routed=False,
        )
        try:
            self.assertEqual(pc.process_clip(clip, self.vault), fake_raw)
        finally:
            ui.ingest, pc._touch_wiki_last_updated_for_url = orig_ingest, orig_touch


# ─── migrate_raws round-trip ─────────────────────────────────────────


class TestMigrateRaws(unittest.TestCase):
    """Highest-blast-radius operation in the refactor — converted 224
    files in one shot. These tests prove the conversion preserves
    metadata fidelity and is idempotent."""

    def test_legacy_to_yaml_preserves_metadata(self):
        import tempfile
        from migrate_raws import _migrate_one
        from raw_parser import read_raw_frontmatter
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "test.md"
            p.write_text(
                "# Probability Lecture Notes — UC Davis\n\n"
                "- **URL:** https://example.com/probability.pdf\n"
                "- **Authors:** Jane Doe\n"
                "- **Captured:** 2026-04-06\n\n"
                "## Abstract\n\nLecture notes content here.\n",
                encoding="utf-8",
            )
            changed, msg = _migrate_one(p)
            self.assertTrue(changed, f"expected changed, got {msg}")
            fm, body = read_raw_frontmatter(p)
            self.assertEqual(fm.get("title"), "Probability Lecture Notes — UC Davis")
            self.assertEqual(fm.get("source"), "https://example.com/probability.pdf")
            self.assertEqual(fm.get("authors"), "Jane Doe")
            self.assertIn("Lecture notes content here", body)

    def test_idempotent_on_yaml(self):
        import tempfile
        from migrate_raws import _migrate_one
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "test.md"
            p.write_text(
                '---\ntitle: "Already YAML"\nsource: "https://example.com"\n'
                'captured_at: "2026-05-09T00:00:00Z"\n---\n\n# x\n\nbody\n',
                encoding="utf-8",
            )
            changed, msg = _migrate_one(p)
            self.assertFalse(changed)
            self.assertEqual(msg, "already YAML")

    def test_no_parseable_structure_skipped(self):
        import tempfile
        from migrate_raws import _migrate_one
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "test.md"
            p.write_text("Just markdown content.\n\nNo header, no bullets.\n",
                         encoding="utf-8")
            changed, msg = _migrate_one(p)
            self.assertFalse(changed)
            self.assertIn("manual review", msg)


# ─── create_wiki_page collision-aware existence check ────────────────


class TestWikiCollisionCheck(_VaultCase):
    """Bug class: Web Clipper drops every LinkedIn post with title
    'Post | LinkedIn'. The previous existence check at wiki_page.py:1242
    was a bare `os.path.exists(wiki_path)` and silently returned
    status=exists for any new LinkedIn post once a redirect stub or any
    real page named 'LinkedIn: Post LinkedIn' existed. Two distinct posts
    therefore lost the second one to the void. These tests pin the new
    behavior: redirect stubs are transparent, URL mismatches disambiguate,
    real duplicates still return exists."""

    def _make_redirect_stub(self, page_name: str, target: str = "Some Other Page"):
        """Plant a redirect stub at wiki/format/webpages/<page_name>.md."""
        wiki_dir = Path(self.vault) / "wiki" / "format" / "webpages"
        wiki_dir.mkdir(parents=True, exist_ok=True)
        stub = wiki_dir / f"{page_name}.md"
        stub.write_text(
            f'---\ntitle: "{page_name}"\nredirect: true\n'
            f'redirect_to: "{target}"\n---\n\n'
            f'> [!info] This page was renamed. Continue to [[{target}]].\n',
            encoding="utf-8",
        )
        return stub

    def _make_real_page(self, page_name: str, source_url: str):
        """Plant a real (non-stub) wiki page with a source: URL."""
        wiki_dir = Path(self.vault) / "wiki" / "format" / "webpages"
        wiki_dir.mkdir(parents=True, exist_ok=True)
        page = wiki_dir / f"{page_name}.md"
        page.write_text(
            f'---\ntitle: "{page_name}"\nsource: "{source_url}"\n'
            f'summary: "x"\nsource_type: "webpage"\ntags: []\n---\n\nbody\n',
            encoding="utf-8",
        )
        return page

    def test_existing_page_matches_url_redirect_stub_is_transparent(self):
        """A redirect stub does NOT occupy the slot for an unrelated URL."""
        from wiki_page import _existing_page_matches_url
        stub = self._make_redirect_stub("LinkedIn: Post LinkedIn")
        self.assertFalse(_existing_page_matches_url(
            str(stub),
            "https://linkedin.com/posts/sakawa_xyz-7457079005138108416-EDmm",
        ))

    def test_existing_page_matches_url_same_url_is_match(self):
        """Real duplicate (same canonical URL) returns True."""
        from wiki_page import _existing_page_matches_url
        url = "https://linkedin.com/posts/somebody_post-1234"
        page = self._make_real_page("Some Real Page", url)
        self.assertTrue(_existing_page_matches_url(str(page), url))

    def test_existing_page_matches_url_different_url_is_mismatch(self):
        """Real page with different URL returns False — caller disambiguates."""
        from wiki_page import _existing_page_matches_url
        page = self._make_real_page(
            "LinkedIn: Post LinkedIn",
            "https://linkedin.com/posts/brijpandeyji_x-1111",
        )
        self.assertFalse(_existing_page_matches_url(
            str(page),
            "https://linkedin.com/posts/sakawa_y-2222",
        ))

    def test_existing_page_matches_url_canonicalized_comparison(self):
        """utm_* params should not break the match. Both sides canonicalized."""
        from wiki_page import _existing_page_matches_url
        page = self._make_real_page(
            "Some Page",
            "https://linkedin.com/posts/somebody_post-1234",
        )
        self.assertTrue(_existing_page_matches_url(
            str(page),
            "https://www.linkedin.com/posts/somebody_post-1234?utm_source=share",
        ))

    def test_disambiguate_title_appends_url_tail(self):
        """Disambiguation produces a distinct, deterministic name.

        Two distinct LinkedIn posts whose chrome-stripped title is
        identical ('Post | LinkedIn') must disambiguate to two distinct
        wiki page names. The exact tail format is implementation-defined
        (driven by url_canonical + slug truncation policy); the contract
        is just *distinct from the original AND from each other*."""
        from wiki_page import _disambiguate_title
        sakawa = _disambiguate_title(
            "LinkedIn: Post LinkedIn",
            "https://linkedin.com/posts/sakawa_ai-runtime-governance-infrastructure-ugcPost-7457079005138108416-EDmm",
            "webpage",
        )
        brij = _disambiguate_title(
            "LinkedIn: Post LinkedIn",
            "https://linkedin.com/posts/brijpandeyji_millions-of-people-7455470610769485824-tF2k",
            "webpage",
        )
        self.assertNotEqual(sakawa, "LinkedIn: Post LinkedIn")
        self.assertNotEqual(brij, "LinkedIn: Post LinkedIn")
        self.assertNotEqual(sakawa, brij)
        self.assertTrue(sakawa.startswith("LinkedIn"))
        self.assertTrue(brij.startswith("LinkedIn"))
        # Determinism: same input → same output
        self.assertEqual(
            sakawa,
            _disambiguate_title(
                "LinkedIn: Post LinkedIn",
                "https://linkedin.com/posts/sakawa_ai-runtime-governance-infrastructure-ugcPost-7457079005138108416-EDmm",
                "webpage",
            ),
        )

    def test_disambiguate_title_handles_missing_url_gracefully(self):
        """No URL → return original title (caller will return exists)."""
        from wiki_page import _disambiguate_title
        title = _disambiguate_title("Some Title", None, "webpage")
        self.assertEqual(title, "Some Title")


# ─── URL-identity collision check (find_wiki_page_for_url) ───────────


class TestFindWikiPageForUrl(_VaultCase):
    """Bug class (top open follow-up at end of 2026-05-10 session): same
    canonical URL captured twice produced two wiki pages because each
    capture derived a different title → different filename → bare
    os.path.exists check missed the duplicate.

    _find_wiki_page_for_url consults inbox/url-resolved.tsv (which already
    maps URL → page_name) BEFORE wiki write. These tests pin the contract."""

    def _seed_tsv(self, rows):
        """Write a url-resolved.tsv with the given rows (list of tuples)."""
        inbox = Path(self.vault) / "inbox"
        inbox.mkdir(parents=True, exist_ok=True)
        tsv = inbox / "url-resolved.tsv"
        body_lines = ["status\tdescription\tsource_url\tresolved_url\ttype"]
        for row in rows:
            body_lines.append("\t".join(row))
        tsv.write_text("\n".join(body_lines) + "\n", encoding="utf-8")
        return tsv

    def _plant_wiki(self, page_name, source_url, subdir="wiki/format/webpages"):
        wiki_dir = Path(self.vault) / subdir
        wiki_dir.mkdir(parents=True, exist_ok=True)
        page = wiki_dir / f"{page_name}.md"
        page.write_text(
            f'---\ntitle: "{page_name}"\nurl: "{source_url}"\n'
            f'source_type: "webpage"\nsummary: "x"\ntags: []\n---\n\nbody\n',
            encoding="utf-8",
        )
        return page

    def test_returns_existing_page_when_tsv_lists_url(self):
        """Hit case: TSV has the URL, wiki file exists → return its path."""
        from wiki_page import _find_wiki_page_for_url
        url = "https://linkedin.com/posts/somebody_post-1234"
        self._seed_tsv([("captured", "Some Real Page", url, url, "webpage")])
        planted = self._plant_wiki("Some Real Page", url)
        found = _find_wiki_page_for_url(url, self.vault)
        self.assertIsNotNone(found)
        self.assertEqual(Path(found).resolve(), planted.resolve())

    def test_returns_none_when_tsv_row_points_at_missing_file(self):
        """TSV row exists but the wiki file was trashed/renamed → return
        None so the normal create flow proceeds. The bug we are NOT
        introducing: incorrectly suppressing fresh captures because of
        stale TSV rows."""
        from wiki_page import _find_wiki_page_for_url
        url = "https://example.com/some-post"
        self._seed_tsv([("captured", "A Trashed Page", url, url, "webpage")])
        # Note: no _plant_wiki call — the file is deliberately absent.
        self.assertIsNone(_find_wiki_page_for_url(url, self.vault))

    def test_match_via_canonicalization(self):
        """Two URL forms that canonicalize to the same canonical URL match.
        e.g. linkedin.com/feed/update/urn:li:ugcPost:ID/ canonicalizes to
        linkedin.com/posts/ugcpost-ID — same source, different shape."""
        from wiki_page import _find_wiki_page_for_url
        canon_form = "https://linkedin.com/posts/ugcpost-7458760655509172224"
        feed_form = "https://www.linkedin.com/feed/update/urn:li:ugcPost:7458760655509172224/"
        self._seed_tsv([("captured", "Praneeta Post", canon_form, canon_form, "webpage")])
        self._plant_wiki("Praneeta Post", canon_form)
        # Lookup with the legacy /feed/update/ form should still hit.
        found = _find_wiki_page_for_url(feed_form, self.vault)
        self.assertIsNotNone(found, "feed/update form should canonicalize to /posts/ugcpost-ID")

    def test_returns_none_when_no_url_match(self):
        """Distinct URL that doesn't match any TSV row → None (write proceeds)."""
        from wiki_page import _find_wiki_page_for_url
        self._seed_tsv([("captured", "Page A", "https://a.example/x", "https://a.example/x", "webpage")])
        self._plant_wiki("Page A", "https://a.example/x")
        self.assertIsNone(_find_wiki_page_for_url("https://b.example/y", self.vault))

    def test_returns_none_when_tsv_missing(self):
        """No url-resolved.tsv → return None (don't crash)."""
        from wiki_page import _find_wiki_page_for_url
        self.assertIsNone(_find_wiki_page_for_url("https://x.example/a", self.vault))

    def test_returns_none_for_empty_url(self):
        """Defensive: empty/None URL → None (no comparison possible)."""
        from wiki_page import _find_wiki_page_for_url
        self._seed_tsv([("captured", "Page", "https://x/y", "https://x/y", "webpage")])
        self.assertIsNone(_find_wiki_page_for_url("", self.vault))
        self.assertIsNone(_find_wiki_page_for_url(None, self.vault))

    def test_skips_malformed_rows_safely(self):
        """A TSV row with too few columns or junk URL must not crash the
        scan — later rows can still match."""
        from wiki_page import _find_wiki_page_for_url
        url = "https://example.com/real"
        # Mix: header (auto-prefixed by _seed_tsv), short row, junk URL row, real row.
        self._seed_tsv([
            ("captured", "Half Row"),  # too few cols
            ("captured", "Junk", "not-a-url-at-all", "x", "webpage"),
            ("captured", "Real Page", url, url, "webpage"),
        ])
        self._plant_wiki("Real Page", url)
        found = _find_wiki_page_for_url(url, self.vault)
        self.assertIsNotNone(found)
        self.assertTrue(found.endswith("Real Page.md"))

    def test_finds_pages_across_all_source_subdirs(self):
        """A repo page (not just webpages/) must also be discoverable."""
        from wiki_page import _find_wiki_page_for_url
        url = "https://github.com/owner/repo"
        # Wiki convention: repo pages are named owner--repo (double-dash).
        self._seed_tsv([("captured", "owner--repo", url, url, "repo")])
        self._plant_wiki("owner--repo", url, subdir="wiki/format/repos")
        found = _find_wiki_page_for_url(url, self.vault)
        self.assertIsNotNone(found)
        self.assertIn("/repos/", found)


# ─── create_wiki_page overwrite path (refresh-wiki backend) ──────────


class TestCreateWikiPageOverwrite(_VaultCase):
    """0.10.8: refresh-wiki uses create_wiki_page(overwrite=True) to
    rewrite an existing page from its raw source. These tests pin:
      - overwrite=True bypasses both collision checks (URL-identity AND
        path-existence) so the refresh actually writes
      - overwrite=True snapshots the prior page to .kb-trash/ for recovery
      - overwrite=False (default) preserves original behavior — duplicates
        return 'exists' instead of clobbering"""

    def _seed_raw(self, slug, title, body, source_url):
        raw_dir = Path(self.vault) / "raw" / "webpages" / "artifacts"
        raw_dir.mkdir(parents=True, exist_ok=True)
        raw = raw_dir / f"{slug}.md"
        raw.write_text(
            f'---\nsource: "{source_url}"\ntitle: "{title}"\n---\n\n# {title}\n\n{body}\n',
            encoding="utf-8",
        )
        return f"raw/webpages/artifacts/{slug}.md"

    def _make_llm_result(self, title, body, summary="Test summary."):
        # Schema requires `summary` so fallback path can't seed pages directly
        # in tests. Provide a minimal llm_result instead.
        return {'title': title, 'summary': summary, 'tags': [], 'related': [], 'body': body}

    def test_overwrite_true_rewrites_existing_page(self):
        """The core refresh-wiki contract: same URL + overwrite=True →
        the existing wiki page gets rewritten, not skipped.

        Note: in the current architecture, llm_result.body is NOT
        written into the wiki page (the wiki is a synthesis stub with
        a *Pending synthesis* placeholder until the LLM regen step
        fills it in). The user-visible delta on refresh is in the
        frontmatter `summary:` field, which create_wiki_page DOES
        copy through. We assert on that to prove the rewrite happened.
        """
        from wiki_page import create_wiki_page
        url = "https://example.com/refresh-target"
        raw_path = self._seed_raw("refresh-target", "Refresh Target", "First-pass body content.", url)
        # First create — normal flow with an llm_result (schema requires summary)
        first = create_wiki_page(
            self.vault, raw_path, url=url, source='test-seed',
            llm_result=self._make_llm_result("Refresh Target", "First-pass body content.",
                                             summary="First-pass summary."),
        )
        self.assertEqual(first['status'], 'created', msg=f"unexpected: {first}")
        first_path = Path(self.vault) / first['wiki_path']
        self.assertIn('First-pass summary.', first_path.read_text(encoding='utf-8'))
        # Simulate a code improvement: rewrite the raw with a different body
        Path(self.vault, raw_path).write_text(
            f'---\nsource: "{url}"\ntitle: "Refresh Target"\n---\n\n'
            f'# Refresh Target\n\nSecond-pass body — much longer than the first '
            f'because the strip got smarter.\n' + ('Padding line.\n' * 30),
            encoding="utf-8",
        )
        bigger_body = "Second-pass body — much longer than the first.\n" + ("Padding line.\n" * 30)
        second = create_wiki_page(
            self.vault, raw_path, url=url, source='test-refresh', overwrite=True,
            llm_result=self._make_llm_result("Refresh Target", bigger_body,
                                             summary="Second-pass summary, after refresh."),
        )
        self.assertEqual(second['status'], 'created')
        # Same wiki path (filename preserved)
        self.assertEqual(second['wiki_path'], first['wiki_path'])
        # Frontmatter summary changed — proves the page was rewritten, not skipped
        new_content = first_path.read_text(encoding='utf-8')
        self.assertIn('Second-pass summary, after refresh.', new_content)
        self.assertNotIn('First-pass summary.', new_content)

    def test_overwrite_true_snapshots_to_trash(self):
        """Refresh must leave a recoverable copy in .kb-trash/ — the
        atomic write would otherwise destroy the prior page. This is
        the recovery contract for `kb undo`.

        Asserts on the frontmatter `summary:` (which IS written into
        the wiki page) rather than the raw body (which is not, per
        the current synthesis-stub architecture)."""
        from wiki_page import create_wiki_page
        url = "https://example.com/snapshot-test"
        raw_path = self._seed_raw("snap", "Snap", "Original body text.", url)
        create_wiki_page(
            self.vault, raw_path, url=url, source='test-seed',
            llm_result=self._make_llm_result("Snap", "Original body text.",
                                             summary="Original summary."),
        )
        # Refresh
        Path(self.vault, raw_path).write_text(
            f'---\nsource: "{url}"\ntitle: "Snap"\n---\n\n# Snap\n\nNew body text.\n',
            encoding="utf-8",
        )
        create_wiki_page(
            self.vault, raw_path, url=url, source='test-refresh', overwrite=True,
            llm_result=self._make_llm_result("Snap", "New body text.",
                                             summary="Updated summary after refresh."),
        )
        # Find the snapshot
        trash_root = Path(self.vault) / ".kb-trash"
        self.assertTrue(trash_root.exists(), "no .kb-trash directory created")
        snapshot_dirs = list(trash_root.glob("*_refresh_wiki"))
        self.assertEqual(len(snapshot_dirs), 1, f"expected 1 snapshot dir, got {snapshot_dirs}")
        snapshot_files = list(snapshot_dirs[0].glob("*.md"))
        self.assertEqual(len(snapshot_files), 1)
        # Snapshot has the ORIGINAL frontmatter (proves it's a copy of
        # the pre-refresh page, not the post-refresh one)
        snapshot_text = snapshot_files[0].read_text(encoding='utf-8')
        self.assertIn('Original summary.', snapshot_text)
        self.assertNotIn('Updated summary after refresh.', snapshot_text)

    def test_overwrite_false_preserves_collision_skip(self):
        """Regression guard: without overwrite, the URL-identity check
        from 0.10.6 still fires — re-issuing kb add returns 'exists'."""
        from wiki_page import create_wiki_page
        url = "https://example.com/no-overwrite"
        raw_path = self._seed_raw("no-ovw", "No Ovw", "Body.", url)
        first = create_wiki_page(
            self.vault, raw_path, url=url, source='test-seed',
            llm_result=self._make_llm_result("No Ovw", "Body."),
        )
        self.assertEqual(first['status'], 'created', msg=f"unexpected: {first}")
        # Second call (no overwrite) — must return exists, NOT rewrite
        second = create_wiki_page(
            self.vault, raw_path, url=url, source='test-rerun',
            llm_result=self._make_llm_result("No Ovw", "Different body — would clobber if overwrite were on."),
        )
        self.assertEqual(second['status'], 'exists')


# ─── kb backfill-assets wiki body rewrite ────────────────────────────


class TestRewriteWikiBodyAssets(_VaultCase):
    """0.10.9: kb backfill-assets used to rewrite only the raw .md file,
    leaving the corresponding wiki page's body still pointing at the
    OLD hot-link URLs. Result: image refs that the raw resolved
    locally would 404 in Obsidian's wiki preview. These tests pin the
    new propagation: after backfill, both raw AND wiki get rewritten
    to the asset's local path (raw uses `../../assets/...`,
    wiki uses `../../../raw/assets/...` — extra `../` because wiki and
    raw are sibling subtrees of the vault root)."""

    def _seed(self, slug, urls_and_locals):
        """Create raw + sidecar + wiki page wired together."""
        import json as _json
        # Sidecar with URL → local-filename mapping
        assets_dir = Path(self.vault) / "raw" / "assets" / slug
        assets_dir.mkdir(parents=True, exist_ok=True)
        sidecar = {
            "page_slug": slug,
            "captured_at": "2026-05-10T00:00:00Z",
            "assets": [{"url": u, "local": l, "alt": "", "bytes": 100}
                       for u, l in urls_and_locals],
            "failures": [],
        }
        (assets_dir / "_assets.json").write_text(_json.dumps(sidecar), encoding="utf-8")
        # Wiki page with body that references the OLD URLs
        wiki_dir = Path(self.vault) / "wiki" / "format" / "webpages"
        wiki_dir.mkdir(parents=True, exist_ok=True)
        wiki_page = wiki_dir / f"{slug}.md"
        body_lines = [f'![]({u})' for u, _ in urls_and_locals]
        wiki_page.write_text(
            f'---\ntitle: "{slug}"\nraw_path: "raw/webpages/artifacts/{slug}.md"\n'
            f'url: "https://example.com/{slug}"\nsource_type: "webpage"\n'
            f'summary: "x"\ntags: []\n---\n\nbody\n\n' + '\n\n'.join(body_lines) + '\n',
            encoding="utf-8",
        )
        return wiki_page

    def test_rewrites_wiki_body_to_wiki_relative_paths(self):
        """Core contract: each old URL in the wiki body becomes the
        wiki-relative form `../../../raw/assets/<slug>/<file>`."""
        from asset_download import rewrite_wiki_body_assets
        urls = [
            ("https://media.licdn.com/foo/img1.jpg", "abc123.jpg"),
            ("https://media.licdn.com/foo/img2.jpg", "def456.jpg"),
        ]
        wiki_page = self._seed("test-slug", urls)
        replaced = rewrite_wiki_body_assets("test-slug", Path(self.vault))
        self.assertEqual(replaced, 2)
        body = wiki_page.read_text(encoding='utf-8')
        # Old URLs gone
        self.assertNotIn("media.licdn.com/foo/img1.jpg", body)
        self.assertNotIn("media.licdn.com/foo/img2.jpg", body)
        # New paths use wiki-relative form (3 dots)
        self.assertIn("![](../../../raw/assets/test-slug/abc123.jpg)", body)
        self.assertIn("![](../../../raw/assets/test-slug/def456.jpg)", body)

    def test_returns_zero_when_no_sidecar(self):
        """No _assets.json → no rewriting, returns 0 (don't crash)."""
        from asset_download import rewrite_wiki_body_assets
        # No setup at all
        self.assertEqual(rewrite_wiki_body_assets("nope", Path(self.vault)), 0)

    def test_returns_zero_when_no_matching_wiki_page(self):
        """Sidecar exists but no wiki page back-references the raw → 0."""
        import json as _json
        from asset_download import rewrite_wiki_body_assets
        slug = "orphan-slug"
        assets_dir = Path(self.vault) / "raw" / "assets" / slug
        assets_dir.mkdir(parents=True, exist_ok=True)
        (assets_dir / "_assets.json").write_text(
            _json.dumps({"assets": [{"url": "https://x/y.jpg", "local": "a.jpg"}]}),
            encoding="utf-8",
        )
        # No wiki page planted
        self.assertEqual(rewrite_wiki_body_assets(slug, Path(self.vault)), 0)

    def test_idempotent_on_already_rewritten_body(self):
        """Run twice → second call replaces 0 (URL already gone from body).
        This guards against the lint churn pattern where backfill keeps
        flagging the same page as 'updated' on every run."""
        from asset_download import rewrite_wiki_body_assets
        urls = [("https://media.licdn.com/x.jpg", "hash.jpg")]
        self._seed("idempotent", urls)
        first = rewrite_wiki_body_assets("idempotent", Path(self.vault))
        second = rewrite_wiki_body_assets("idempotent", Path(self.vault))
        self.assertEqual(first, 1)
        self.assertEqual(second, 0)

    def test_skips_links_that_are_not_asset_refs(self):
        """A wiki body URL that ISN'T in the sidecar mapping must pass
        through untouched — the rewriter is conservative, not aggressive."""
        from asset_download import rewrite_wiki_body_assets
        # Sidecar maps only one URL
        urls = [("https://media.licdn.com/known.jpg", "k.jpg")]
        wiki_page = self._seed("conservative", urls)
        # Append an unrelated link to the wiki body
        body = wiki_page.read_text(encoding='utf-8')
        wiki_page.write_text(body + '\n\n[Unrelated source](https://other.com/page)\n', encoding='utf-8')
        rewrite_wiki_body_assets("conservative", Path(self.vault))
        new_body = wiki_page.read_text(encoding='utf-8')
        # The unrelated link survives intact
        self.assertIn("[Unrelated source](https://other.com/page)", new_body)


# ─── LinkedIn chrome stripping ───────────────────────────────────────


class TestLinkedInChromeStrip(_VaultCase):
    """User feedback: every LinkedIn raw started with the user's own
    profile sidebar (Chong Xu photo + 'Reactivate Premium' upsells +
    nav links). The actual post is sandwiched between `## Feed post`
    (LinkedIn's DOM section header) and the comments footer.
    These tests pin the stripper's contract."""

    LEADING_CHROME = (
        "[\n\n"
        "![Chong Xu](https://media.licdn.com/dms/image/v2/foo/profile-framedphoto-shrink_200_200/foo)\n\n"
        "](https://www.linkedin.com/in/xuchong/)"
        "[Achieve 4x more profile visits](https://www.linkedin.com/premium/redeem/?foo)\n\n"
        "[\n\nReactivate Premium: 50% Off\n\n](https://www.linkedin.com/premium/redeem/?foo)\n\n"
    )
    POST_MARKER = "## Feed post![View Praneeta D.’s profile](https://media.licdn.com/dms/image/v2/foo/profile-displayphoto-shrink_100_100/foo)\n\n"
    PROFILE_NAV = "View Praneeta D.’s profile\n\n"
    POST_BODY = (
        "ClaudeBleed is not merely a browser extension flaw. It is another warning that the AI attack surface is no longer expanding linearly. It is compounding.\n\n"
        "LayerX reports that Claude in Chrome could be hijacked by another Chrome extension because the extension trusted the browser origin instead of the execution context.\n\n"
        "[**#AISecurity**](https://www.linkedin.com/search/results/all/?keywords=%23aisecurity)"
    )
    TRAILING_CHROME = "\n\n---\n\nBe the first to comment\n"

    def test_strips_leading_chrome(self):
        from process_clip import _strip_linkedin_chrome
        full = self.LEADING_CHROME + self.POST_MARKER + self.PROFILE_NAV + self.POST_BODY
        stripped = _strip_linkedin_chrome(full)
        self.assertNotIn("Chong Xu", stripped)
        self.assertNotIn("Reactivate Premium", stripped)
        self.assertNotIn("Achieve 4x more profile visits", stripped)
        self.assertNotIn("View Praneeta D.’s profile", stripped)
        self.assertNotIn("## Feed post", stripped)
        self.assertTrue(stripped.startswith("ClaudeBleed"))

    def test_strips_trailing_chrome(self):
        from process_clip import _strip_linkedin_chrome
        full = self.LEADING_CHROME + self.POST_MARKER + self.PROFILE_NAV + self.POST_BODY + self.TRAILING_CHROME
        stripped = _strip_linkedin_chrome(full)
        self.assertNotIn("Be the first to comment", stripped)
        # Body content preserved
        self.assertIn("AISecurity", stripped)

    def test_strips_n_reactions_footer(self):
        from process_clip import _strip_linkedin_chrome
        full = self.LEADING_CHROME + self.POST_MARKER + self.PROFILE_NAV + self.POST_BODY + "\n\n42 reactions\n"
        stripped = _strip_linkedin_chrome(full)
        self.assertNotIn("42 reactions", stripped)

    def test_strips_carousel_preview_block(self):
        """0.9.14: LinkedIn carousel posts append a preview block to the
        body — post-title echo + bullet (·) + 'N pages'. Strip it.
        Without this, the user sees '8 pages' as the last visible content
        of the raw with no images (because Web Clipper doesn't capture
        lazy-loaded carousel pages 2-N anyway). Cosmetic but reduces
        confusion about what was captured."""
        from process_clip import _strip_linkedin_chrome
        body = (
            self.POST_BODY
            + "\n\nAI Attack Surface Compounding\n\n·\n\n8 pages\n\n---\n"
        )
        stripped = _strip_linkedin_chrome(body)
        self.assertNotIn("8 pages", stripped)
        self.assertNotIn("AI Attack Surface Compounding", stripped)
        # Body content itself is preserved
        self.assertIn("AISecurity", stripped)

    def test_strips_n_comments_footer(self):
        from process_clip import _strip_linkedin_chrome
        full = self.LEADING_CHROME + self.POST_MARKER + self.PROFILE_NAV + self.POST_BODY + "\n\n## 17 comments\n"
        stripped = _strip_linkedin_chrome(full)
        self.assertNotIn("17 comments", stripped)

    def test_no_marker_returns_unchanged(self):
        """Body without `## Feed post` marker passes through. Conservative
        fallback so non-LinkedIn pages or unrecognized LinkedIn variants
        never accidentally lose content."""
        from process_clip import _strip_linkedin_chrome
        body = "Some random content with no LinkedIn marker.\n\nMore text."
        stripped = _strip_linkedin_chrome(body)
        self.assertEqual(stripped, body)

    def test_runs_only_for_linkedin_urls_in_process_clip(self):
        """Non-LinkedIn URLs skip the stripper entirely (per host check
        in process_clip, not the stripper itself). Verifies the integration."""
        from process_clip import process_clip
        clip_dir = Path(self.vault) / "clippings"
        clip_dir.mkdir(exist_ok=True)
        clip = clip_dir / "x-tweet.md"
        clip.write_text(
            '---\ntitle: "X Tweet"\nsource: "https://x.com/somebody/status/123"\n'
            'created: 2026-05-10\ntags:\n  - "clippings"\n---\n\n'
            '# X Tweet\n\n## Feed post\n\nThis content should NOT be stripped on non-LinkedIn.',
            encoding="utf-8",
        )
        path = process_clip(clip, self.vault)
        text = Path(path).read_text(encoding="utf-8")
        # The "## Feed post" marker survives on non-LinkedIn (stripper didn't run)
        self.assertIn("## Feed post", text)

    def test_linkedin_clip_strips_at_write_time(self):
        """End-to-end: LinkedIn clip → process_clip → raw artifact has no
        leading chrome. The user-visible bug we're closing."""
        from process_clip import process_clip
        clip_dir = Path(self.vault) / "clippings"
        clip_dir.mkdir(exist_ok=True)
        clip = clip_dir / "li.md"
        full = self.LEADING_CHROME + self.POST_MARKER + self.PROFILE_NAV + self.POST_BODY + self.TRAILING_CHROME
        clip.write_text(
            '---\ntitle: "Post | LinkedIn"\n'
            'source: "https://www.linkedin.com/posts/somebody_test-7458999"\n'
            'created: 2026-05-10\ntags:\n  - "clippings"\n---\n\n'
            f'{full}',
            encoding="utf-8",
        )
        path = process_clip(clip, self.vault)
        text = Path(path).read_text(encoding="utf-8")
        self.assertNotIn("Chong Xu", text)
        self.assertNotIn("Reactivate Premium", text)
        self.assertNotIn("Be the first to comment", text)
        self.assertIn("ClaudeBleed", text)

    def test_strips_trailing_only_when_leading_already_gone(self):
        """0.9.9 regression: existing raws that came in via the URL-only
        kb-add path have leading chrome already stripped (browserCapture
        strips the H1+chrome). Their trailing chrome must STILL get
        stripped on a re-process."""
        from process_clip import _strip_linkedin_chrome
        body = self.POST_BODY + self.TRAILING_CHROME  # NO leading marker
        stripped = _strip_linkedin_chrome(body)
        self.assertNotIn("Be the first to comment", stripped)
        self.assertIn("AISecurity", stripped)

    def test_post_images_preserved_through_strip(self):
        """0.9.11 regression: Web Clipper concatenates `![View image](feedshare-url)`
        onto the same line as trailing chrome ('Enjoy this? Repost it to your
        network and follow X for more![View image](url)'). The trailing-marker
        cut would otherwise eat the image with the chrome. Post images must
        survive — profile photos must NOT (they're chrome)."""
        from process_clip import _strip_linkedin_chrome
        body = (
            self.POST_BODY
            + "\n\nEnjoy this? Repost it to your network and follow X for more"
            + "![View image](https://media.licdn.com/dms/image/v2/foo/feedshare-shrink_1280/bar.jpg)"
            + "\n\n8\nLike\nComment\nShare\n"
            # Profile photo embedded in trailing chrome — should NOT be preserved
            + "![View X's profile](https://media.licdn.com/dms/image/v2/baz/profile-displayphoto-shrink_100_100/qux.jpg)"
        )
        stripped = _strip_linkedin_chrome(body)
        # Post image survives
        self.assertIn("feedshare-shrink_1280", stripped)
        # Profile photo (chrome) does NOT
        self.assertNotIn("profile-displayphoto", stripped)
        # And the actual chrome text is gone
        self.assertNotIn("Enjoy this?", stripped)
        self.assertNotIn("Like", stripped[stripped.find("AISecurity"):])  # downstream of post

    def test_strips_via_follow_button_variant(self):
        """0.10.18: LinkedIn shows different end-of-author-header buttons
        based on the user's relationship to the post author:
          - Connect (not in network)
          - Follow (followed OR too many connections)
          - Following (already following — toggle)
        Branch B's anchor regex must catch ALL three. Without 'Follow'
        coverage, Zhaorun-class single-image posts (where the author
        button is 'Follow') leak the entire user-profile + author-bio
        chrome into the title."""
        from process_clip import _strip_linkedin_chrome
        body = (
            "Chong Xu\n\n"
            "Technologist, Investor, entrepreneur\n\n"
            "Reactivate Premium: 50% Off\n\n"
            "Profile viewers\n\n144\n\n"
            "Zhaorun Chen\n\n"
            "CS PhD Student at The University of Chicago\n\n"
            "1d • \n\n"
            "Follow\n\n"
            "AI agents are already going wild, but today's red-teaming tools "
            "for them are still like toys.\n\n"
            "More body content here.\n"
        )
        stripped = _strip_linkedin_chrome(body)
        # User chrome is gone
        self.assertNotIn("Chong Xu", stripped)
        self.assertNotIn("Technologist, Investor", stripped)
        # Post author chrome is gone
        self.assertNotIn("CS PhD Student", stripped)
        self.assertNotIn("Zhaorun Chen", stripped)
        # The actual post body is preserved
        self.assertIn("AI agents are already going wild", stripped)

    def test_strips_authenticated_dom_via_premium_sidebar_markers(self):
        """0.10.17: Branch C fallback. capture-deep against a single-image
        LinkedIn post can produce <main>.innerText that lacks BOTH the
        '## Feed post' marker (markdown header collapsed) AND the Connect
        button (single-image post layout doesn't show it in <main>). The
        only chrome signals left are the LinkedIn premium sidebar text:
        'Reactivate Premium', 'Profile viewers', 'Post impressions'.
        Zhaorun Chen's re-capture surfaced this; chrome strip then needs
        to use those markers as the strip anchor + skip to the first
        substantive content line."""
        from process_clip import _strip_linkedin_chrome
        body = (
            "Chong Xu\n\n"
            "Technologist, Investor, entrepreneur \n\n"
            "Sunnyvale, California\n\n"
            "Self-employed\n\n"
            "Achieve 4x more profile visits\n\n"
            "Reactivate Premium: 50% Off\n\n"
            "Profile viewers\n\n144\n\n"
            "Post impressions\n\n866\n\n"
            "AI agents are already going wild but today, we're open-sourcing DTap. "
            "Built over 20 months with $120K in API credits across 50+ environments.\n\n"
            "More post body content here.\n"
        )
        stripped = _strip_linkedin_chrome(body)
        # User's profile chrome is gone
        self.assertNotIn("Chong Xu", stripped)
        self.assertNotIn("Sunnyvale", stripped)
        self.assertNotIn("Reactivate Premium", stripped)
        self.assertNotIn("Profile viewers", stripped)
        self.assertNotIn("Technologist, Investor", stripped)
        # Real post content survives
        self.assertIn("AI agents are already going wild", stripped)
        self.assertIn("DTap", stripped)

    def test_strips_authenticated_dom_user_chrome_via_connect_button(self):
        """0.10.5: capture-deep against authenticated LinkedIn DOM produces
        a body that includes the user's own profile sidebar BEFORE the
        plain-text 'Feed post' marker. The leading-strip fallback uses
        the 'Connect' button (which always appears right after the
        author header) as the cut point. This test reproduces the
        Praneeta capture-deep bug witnessed during 0.10.0 dev."""
        from process_clip import _strip_linkedin_chrome
        body = (
            "Technologist, Investor, entrepreneur\n\n"
            "Chong Xu\n\n"
            "Sunnyvale, California\n\n"
            "Self-employed\n\n"
            "Achieve 4x more profile visits\n\n"
            "Reactivate Premium: 50% Off\n\n"
            "Profile viewers\n\n144\n\n"
            "Post impressions\n\n952\n\n"
            "Feed post\n\n"
            "Praneeta D.\n\n • 2nd\n\n"
            "AI Security Leader bio.\n\n"
            "1d •\n\n"
            "Connect\n\n"
            + self.POST_BODY
        )
        stripped = _strip_linkedin_chrome(body)
        # User's profile chrome is gone
        self.assertNotIn("Chong Xu", stripped)
        self.assertNotIn("Sunnyvale", stripped)
        self.assertNotIn("Profile viewers", stripped)
        self.assertNotIn("Reactivate Premium", stripped)
        # Author header is gone
        self.assertNotIn("Praneeta D.", stripped)
        self.assertNotIn("AI Security Leader bio", stripped)
        self.assertNotIn("Connect", stripped[:200])  # not in early chrome region
        # Actual post body survives
        self.assertIn("ClaudeBleed", stripped)

    def test_strips_author_profile_nav_after_connect_cut(self):
        """0.10.6: capture-deep against the Praneeta post left a leading
        'View Praneeta D.'s profile' line in the wiki body after the
        Connect-button cut. The profile-nav strip used to live inside
        Branch A (original `## Feed post` marker) only — Branch B
        (Connect-button fallback) never applied it. Moved out so both
        paths converge on the same sanitizer."""
        from process_clip import _strip_linkedin_chrome
        body = (
            "Chong Xu\n\n"
            "Sunnyvale, California\n\n"
            "Reactivate Premium: 50% Off\n\n"
            "Feed post\n\n"
            "Praneeta D.\n\n • 2nd\n\n"
            "AI Security Leader bio.\n\n"
            "1d •\n\n"
            "Connect\n\n"
            "View Praneeta D.’s profile\n\n"  # the surviving profile-nav line
            + self.POST_BODY
        )
        stripped = _strip_linkedin_chrome(body)
        self.assertNotIn("View Praneeta D.’s profile", stripped)
        self.assertIn("ClaudeBleed", stripped)  # actual content preserved

    def test_strips_author_profile_nav_via_original_feed_post_marker(self):
        """Regression guard: the original Branch A path (the `## Feed post`
        marker, used for non-capture-deep clips) must STILL strip the
        profile-nav line after the move out of Branch A."""
        from process_clip import _strip_linkedin_chrome
        body = self.POST_MARKER + self.PROFILE_NAV + self.POST_BODY
        stripped = _strip_linkedin_chrome(body)
        self.assertNotIn("View Praneeta D.’s profile", stripped)
        self.assertIn("ClaudeBleed", stripped)

    def test_profile_nav_line_in_non_linkedin_clip_preserved(self):
        """Conservativity guard: a clip that doesn't trigger any LinkedIn
        leading marker should NOT have its content stripped, even if it
        happens to contain a 'View X's profile' line. Without the
        `if leading:` gate this would silently mutate non-LinkedIn clips."""
        from process_clip import _strip_linkedin_chrome
        body = (
            "Some unrelated article body\n\n"
            "View Alice Smith's profile\n\n"  # text in body — not chrome
            "More content here\n"
        )
        stripped = _strip_linkedin_chrome(body)
        self.assertIn("View Alice Smith's profile", stripped)

    def test_post_image_already_in_clean_region_not_duplicated(self):
        """Dedup: if the post image survives the strip naturally (because it
        was BEFORE the trailing marker), don't re-attach a duplicate."""
        from process_clip import _strip_linkedin_chrome
        body = (
            self.POST_BODY
            + "\n\n![Post image](https://media.licdn.com/dms/image/v2/foo/feedshare-shrink_1280/bar.jpg)"
            + "\n\nEnjoy this? Repost it to your network and follow X for more"
        )
        stripped = _strip_linkedin_chrome(body)
        # Should appear exactly once
        self.assertEqual(stripped.count("feedshare-shrink_1280"), 1)

    def test_strips_jim_libby_chrome_variants(self):
        """0.9.9: new trailing markers for Jim Libby-style chrome —
        'Enjoy this? Repost', 'To view or add a comment', 'Like\\nComment\\nShare'
        action row, 'More from this author', 'Explore content categories',
        'N followers'."""
        from process_clip import _strip_linkedin_chrome
        cases = [
            ("\n\nEnjoy this? Repost it to your network and follow X for more\n", "repost CTA"),
            ("\n\nTo view or add a comment, sign in\n", "auth-wall comment CTA"),
            ("\n\n8\nLike\nComment\nShare\n", "reactions+actions row"),
            ("\n\n## More from this author\n", "related-posts header"),
            ("\n\n## Explore content categories\n", "categories footer"),
            ("\n\n29,818 followers\n", "followers count"),
        ]
        for trailing, label in cases:
            with self.subTest(label=label):
                body = self.POST_BODY + trailing + "EXTRA CHROME THAT MUST BE GONE"
                stripped = _strip_linkedin_chrome(body)
                self.assertNotIn("EXTRA CHROME THAT MUST BE GONE", stripped, label)
                self.assertIn("AISecurity", stripped, label)


# ─── Title fallback rejects chrome-stripped page titles ──────────────


class TestBuildFallbackDataTitleRejection(_VaultCase):
    """0.9.9: when hint_title is a chrome-stripped LinkedIn/X page title
    ('Post | LinkedIn', 'Feed | LinkedIn', etc.), build_fallback_data
    must reject it and derive a meaningful fallback from body content.

    Without this, every LinkedIn post wiki page got the same title
    'LinkedIn: Post LinkedIn' — confusing in Dataview tables and
    forcing the user to kb-rename every page individually."""

    def test_rejects_post_pipe_linkedin(self):
        from wiki_page import build_fallback_data
        body = "Are VCs hot or cold on your industry? Data from 1,000+ Series A rounds.\n\nMore body."
        data = build_fallback_data(
            body, "https://linkedin.com/posts/peterjameswalker_x", "webpage",
            hint_title="Post | LinkedIn",
        )
        self.assertNotIn("Post LinkedIn", data["title"])
        self.assertIn("VCs hot or cold", data["title"])

    def test_rejects_feed_pipe_linkedin(self):
        from wiki_page import build_fallback_data
        body = "Substantive post body about something interesting that should become the title."
        data = build_fallback_data(
            body, "https://linkedin.com/feed", "webpage",
            hint_title="Feed | LinkedIn",
        )
        self.assertNotIn("Feed LinkedIn", data["title"])
        self.assertIn("Substantive post", data["title"])

    def test_rejects_sign_up_pipe_linkedin(self):
        """Auth-wall captures arrive with title 'Sign Up | LinkedIn'.
        Don't propagate that into the wiki page name."""
        from wiki_page import build_fallback_data
        body = "Some captured content even though it was an auth wall page."
        data = build_fallback_data(
            body, "https://linkedin.com/posts/somebody_x", "webpage",
            hint_title="Sign Up | LinkedIn",
        )
        self.assertNotIn("Sign Up LinkedIn", data["title"])

    def test_keeps_legitimate_titles(self):
        """Sanity: non-collision-bait titles still pass through unchanged."""
        from wiki_page import build_fallback_data
        body = "Body content."
        data = build_fallback_data(
            body, "https://example.com/foo", "webpage",
            hint_title="A Real Article About Distributed Systems",
        )
        self.assertIn("Distributed Systems", data["title"])


# ─── Embedded image URL in title fallback ────────────────────────────


class TestBuildFallbackDataImageURLRejection(_VaultCase):
    """2026-05-21: LinkedIn hashtag rows like
    `[**#tag1**](url1) [**#tag2**](url2)![View image](https://media.licdn.com/...)`
    slipped through build_fallback_data's body scan — the line starts
    with `[**` (not in the prefix-skip set), so the whole line (including
    the embedded image URL) became the title candidate. apply_naming_
    convention then stripped the URL's slashes/colons into a slug-shaped
    garbage like 'LinkedIn: — https:media.licdn.comdmsimagev2D5622AQFS3
    mmaNDdvvQfeeds'. Two raws were affected — Chuck Herrin's 'Compression
    Is Attack Surface' post and the linkedin-com-feed.md feed snapshot."""

    def test_rejects_line_with_embedded_licdn_image(self):
        from wiki_page import build_fallback_data
        body = (
            "OMG. I get it. After two days at a course I understand why AI "
            "is fundamentally insecurable.\n\n"
            "[**#CracksInYourFoundationModels**](https://www.linkedin.com/search/results/all/?keywords=%23cracksinyourfoundationmodels)"
            " [**#AISecurity**](https://www.linkedin.com/search/results/all/?keywords=%23aisecurity)"
            "![View image](https://media.licdn.com/dms/image/v2/D5622AQFS3mmaNDdvvQ/feedshare-shrink_1280/B56Z5H.ZaUIoAM-/0/1779324000157)"
        )
        data = build_fallback_data(
            body,
            "https://linkedin.com/posts/ugcpost-7463025773600333824",
            "webpage",
            hint_title="Post | LinkedIn",  # generic — forces body scan
        )
        # The hashtag+image line must NOT become the title — the OMG
        # prose line on the first paragraph is the only valid candidate.
        self.assertNotIn("licdn", data["title"].lower())
        self.assertNotIn("media.", data["title"].lower())
        self.assertNotIn("dmsimage", data["title"].lower())
        self.assertIn("OMG", data["title"])

    def test_rejects_line_with_embedded_twimg(self):
        """Same class on the X side: pbs.twimg.com embedded image URLs
        must not propagate into the title."""
        from wiki_page import build_fallback_data
        body = (
            "Real tweet content that explains a substantive idea worth indexing.\n\n"
            "[link text](https://t.co/abc)"
            "![](https://pbs.twimg.com/media/Fy_abc123.jpg)"
        )
        data = build_fallback_data(
            body,
            "https://x.com/someone/status/1234567890",
            "webpage",
            hint_title="X",  # generic — forces body scan
        )
        self.assertNotIn("twimg", data["title"].lower())
        self.assertNotIn("pbs.", data["title"].lower())
        self.assertIn("Real tweet content", data["title"])

    def test_apply_naming_convention_rejects_url_shaped_residual(self):
        """Defense in depth: if a URL-shaped title still reaches
        apply_naming_convention (e.g. a hint_title that's just a URL),
        the residual-URL check must replace it with a URL-derived slug
        before the filename sanitizer would strip slashes/colons and
        produce slug-garbage."""
        from wiki_page import apply_naming_convention
        result = apply_naming_convention(
            "https://media.licdn.com/dms/image/v2/D5622AQFS3mmaNDdvvQ/feedshare-shrink_1280/foo",
            "https://linkedin.com/posts/ugcpost-7463025773600333824",
            "webpage",
        )
        self.assertNotIn("dmsimage", result.lower())
        self.assertNotIn("licdn", result.lower())
        # The URL-derived fallback uses the path (posts/ugcpost-...) so
        # the post ID survives — that's still distinguishable.
        self.assertIn("LinkedIn:", result)

    def test_apply_naming_convention_keeps_legitimate_titles(self):
        """Sanity: titles that aren't URL-shaped pass through unchanged
        — only the URL-shaped-residual check is new, not a global rewrite."""
        from wiki_page import apply_naming_convention
        result = apply_naming_convention(
            "Compression Is Attack Surface",
            "https://linkedin.com/posts/ugcpost-7463025773600333824",
            "webpage",
        )
        self.assertEqual(result, "LinkedIn: Compression Is Attack Surface")

    def test_apply_naming_convention_strips_x_title_chrome(self):
        """X.com's page <title> is '<DisplayName> on X: "<tweet>…" / X'.
        The cleaner must reduce it to the tweet headline (no author, no
        doubled 'X:', no leading backslash from a mangled \\"). Witnessed
        2026-05-26: 'elvis on X: "New research…"' produced the broken title
        'X: elvis on X: \\New research…'."""
        from wiki_page import apply_naming_convention
        url = "https://x.com/omarsar0/status/2058936160291004483"
        # The first-line form the pipeline actually feeds in.
        self.assertEqual(
            apply_naming_convention(
                'elvis on X: "New research from Microsoft Research', url, "webpage"),
            "X: New research from Microsoft Research")
        # The already-mangled form (opening quote turned into a backslash).
        self.assertEqual(
            apply_naming_convention(
                "elvis on X: \\New research from Microsoft Research", url, "webpage"),
            "X: New research from Microsoft Research")
        # Trailing ' / X' chrome and a t.co tail are also stripped.
        self.assertEqual(
            apply_naming_convention(
                'someone on X: "Hello world https://t.co/abc123" / X', url, "webpage"),
            "X: Hello world")
        # Guard: a legitimate tweet that merely contains 'on X:' (no quote
        # right after) is NOT over-stripped of its author-less content.
        self.assertEqual(
            apply_naming_convention("My take on X: it is great", url, "webpage"),
            "X: My take on X: it is great")

    def test_apply_naming_convention_strips_obsidian_forbidden_chars(self):
        """`#^[]` must never reach a wiki page name: Obsidian parses them as
        heading/block anchors and link delimiters inside [[wikilinks]], so the
        page becomes unclickable. Witnessed 2026-05-28: a LinkedIn hashtag-row
        title 'LinkedIn: #agenticai #aisecurity …' produced a dead link."""
        from wiki_page import apply_naming_convention
        url = "https://www.linkedin.com/posts/itsecuritypartners_x-7463304018908532737-zjNp"
        out = apply_naming_convention(
            "#agenticai #aisecurity #securityoperations #mdr #dfir", url, "webpage")
        self.assertNotRegex(out, r"[#^\[\]]")          # no forbidden char survives
        self.assertEqual(out, "LinkedIn: agenticai aisecurity securityoperations mdr dfir")
        # Mixed real-text titles keep their words, lose only the forbidden chars.
        self.assertEqual(
            apply_naming_convention("Issue #42 [cs.AI] ^ref", url, "webpage"),
            "LinkedIn: Issue 42 cs.AI ref")


# ─── Asset path rewrite for wiki ─────────────────────────────────────


class TestRewriteAssetPathsForWiki(unittest.TestCase):
    """0.9.12: raw-relative asset paths (`../../assets/<slug>/<file>`) are
    correct from `raw/<cat>/artifacts/` (depth 3, two levels up = `raw/`),
    but break when the body is copied verbatim into a wiki page at
    `wiki/format/<cat>/` (where `../../` resolves to `wiki/` instead).
    The wiki version needs `../../../raw/assets/<slug>/<file>`."""

    def test_basic_asset_path_rewrite(self):
        from wiki_page import _rewrite_asset_paths_for_wiki
        body = "![alt](../../assets/some-slug/abc123.jpg)"
        rewritten = _rewrite_asset_paths_for_wiki(body)
        self.assertEqual(rewritten, "![alt](../../../raw/assets/some-slug/abc123.jpg)")

    def test_multiple_asset_refs_in_body(self):
        from wiki_page import _rewrite_asset_paths_for_wiki
        body = (
            "First image: ![](../../assets/slug-a/one.png)\n"
            "Then text. Second: ![alt](../../assets/slug-b/two.gif)\n"
            "Third inline: ![](../../assets/slug-c/three.webp)"
        )
        rewritten = _rewrite_asset_paths_for_wiki(body)
        self.assertNotIn("](../../assets/", rewritten)
        self.assertEqual(rewritten.count("](../../../raw/assets/"), 3)

    def test_non_asset_links_unchanged(self):
        """Wikilinks, http URLs, anything else with `../../` that isn't
        `](../../assets/` must not be touched."""
        from wiki_page import _rewrite_asset_paths_for_wiki
        body = (
            "Wikilink: [[Some Page]]\n"
            "External: [text](https://example.com)\n"
            "Other relative: [doc](../../docs/foo.pdf)\n"
            "Asset: ![](../../assets/x/y.jpg)"
        )
        rewritten = _rewrite_asset_paths_for_wiki(body)
        self.assertIn("[[Some Page]]", rewritten)
        self.assertIn("(https://example.com)", rewritten)
        self.assertIn("(../../docs/foo.pdf)", rewritten)  # unchanged
        self.assertIn("(../../../raw/assets/x/y.jpg)", rewritten)  # rewritten

    def test_empty_body_returns_empty(self):
        from wiki_page import _rewrite_asset_paths_for_wiki
        self.assertEqual(_rewrite_asset_paths_for_wiki(""), "")
        self.assertEqual(_rewrite_asset_paths_for_wiki(None), None)


# ─── 0.9.13 — URL canon, raw-side bait titles, Unicode typography ────


class TestLinkedInFeedUpdateCanonicalization(unittest.TestCase):
    """0.9.13 + 0.10.12: LinkedIn presents the same post under THREE URL
    forms:
      * /posts/<author>_<slug>-ugcPost-<id>-<token>  (post detail view / Web Clipper)
      * /feed/update/urn:li:ugcPost:<id>/             (feed view / in-app share)
      * /posts/ugcpost-<id>                           (URN-only canonical)

    All three must collapse to /posts/ugcpost-<id> so the same post
    captured via different URL forms produces ONE wiki page. Without the
    cross-format collapse (added 0.10.12), the writer-side URL collision
    check (0.10.6) and the lint duplicate-URL detector (0.10.9) both
    miss the duplicate and the user ends up with two pages for the same
    post — which is exactly what happened in the 0.10.11 evening."""

    def test_feed_update_ugcpost_canonicalized(self):
        from url_canonical import canonicalize
        result = canonicalize("https://www.linkedin.com/feed/update/urn:li:ugcPost:7458760655509172224/")
        self.assertEqual(result.url, "https://linkedin.com/posts/ugcpost-7458760655509172224")

    def test_feed_update_activity_canonicalized(self):
        from url_canonical import canonicalize
        result = canonicalize("https://www.linkedin.com/feed/update/urn:li:activity:1234567890")
        self.assertEqual(result.url, "https://linkedin.com/posts/ugcpost-1234567890")

    def test_two_feed_update_clips_collapse_to_same_canon(self):
        """Idempotent same-URL collapse: re-clipping the same /feed/update
        link gets the same slug, so no duplicate raws."""
        from url_canonical import canonicalize
        a = canonicalize("https://www.linkedin.com/feed/update/urn:li:ugcPost:999/").url
        b = canonicalize("https://linkedin.com/feed/update/urn:li:ugcPost:999").url
        self.assertEqual(a, b)

    def test_author_handle_form_collapses_to_urn_only(self):
        """0.10.12 fix: the Praneeta-2 incident. /posts/<author>_<slug>-ugcPost-<id>-<variant>
        form must collapse to /posts/ugcpost-<id> so it matches BOTH the
        in-app feed/update form AND the URN-only form. Previously this
        case slipped past both the writer guard and lint detector."""
        from url_canonical import canonicalize
        result = canonicalize("https://www.linkedin.com/posts/praneetaparadkar_ai-attack-surface-compounding-ugcPost-7458760655509172224-RJvM")
        self.assertEqual(result.url, "https://linkedin.com/posts/ugcpost-7458760655509172224")

    def test_all_three_forms_collapse_to_same_canon(self):
        """The strict end-to-end contract: every URL form for the same
        post produces the same canonical. This is the test that would
        have failed BEFORE 0.10.12 — proving the duplicate-page bug
        class is now structurally closed at the canonicalizer layer."""
        from url_canonical import canonicalize
        forms = [
            "https://www.linkedin.com/posts/praneetaparadkar_ai-attack-surface-compounding-ugcPost-7458760655509172224-RJvM",
            "https://linkedin.com/posts/ugcpost-7458760655509172224",
            "https://www.linkedin.com/feed/update/urn:li:ugcPost:7458760655509172224/",
        ]
        canons = {canonicalize(u).url for u in forms}
        self.assertEqual(len(canons), 1, f"all three forms should collapse, got: {canons}")

    def test_activity_urn_form_collapses(self):
        """LinkedIn also uses /posts/<handle>_<slug>-activity-<id>-<token>
        (older posts). Must collapse the same way."""
        from url_canonical import canonicalize
        result = canonicalize("https://linkedin.com/posts/jim-libby-ph-d-2788a8_this-is-the-way-software-has-always-wor-activity-7458055667612770304-x9oi")
        self.assertEqual(result.url, "https://linkedin.com/posts/ugcpost-7458055667612770304")

    def test_bare_handle_with_no_urn_passes_through(self):
        """Conservativity: a /posts/ URL that DOESN'T embed a URN ID
        (very rare; profile-pinned post or company page) must NOT be
        mangled — we can't make up a URN that doesn't exist."""
        from url_canonical import canonicalize
        result = canonicalize("https://linkedin.com/posts/some-author-only")
        self.assertIn("some-author-only", result.url)


class TestPlaywrightAutoPromote(_VaultCase):
    """0.10.14: process_clip auto-promotes Web Clipper captures of known-
    broken-via-Web-Clipper domains (LinkedIn today) to capture-deep, which
    fetches via Playwright + saved auth and produces richer raws (full
    body, all carousel images, real titles with author attribution).

    Three guards must all hold to promote:
      1. ATHENA_DISABLE_PLAYWRIGHT_PROMOTE env var unset
      2. URL on _PLAYWRIGHT_DOMAINS list
      3. clipped_via != deep-capture (avoid recursion)
      4. Playwright auth marker exists (saved session)

    These tests pin the predicate logic. The actual subprocess invocation
    of capture-deep is integration-tested manually — too costly to drive
    Playwright from a unit test."""

    def test_returns_false_when_env_var_disables(self):
        """Test escape hatch: ATHENA_DISABLE_PLAYWRIGHT_PROMOTE=1 force-disables."""
        from process_clip import _should_promote_to_playwright
        # _VaultCase.setUp already sets the env var; this just confirms the contract.
        self.assertFalse(_should_promote_to_playwright(
            "https://linkedin.com/posts/somebody-12345",
            {"clipped_via": "web-clipper"},
        ))

    def test_returns_false_for_non_listed_domain(self):
        """X.com works fine via Web Clipper per user testing 2026-05-10
        — only LinkedIn is on the auto-promote list. This pins that
        decision; adding x.com would require user-observed failures first."""
        import os as _os
        from process_clip import _should_promote_to_playwright
        _os.environ.pop("ATHENA_DISABLE_PLAYWRIGHT_PROMOTE", None)
        try:
            self.assertFalse(_should_promote_to_playwright(
                "https://x.com/somebody/status/12345",
                {"clipped_via": "web-clipper"},
            ))
            self.assertFalse(_should_promote_to_playwright(
                "https://example.com/article",
                {"clipped_via": "web-clipper"},
            ))
        finally:
            _os.environ["ATHENA_DISABLE_PLAYWRIGHT_PROMOTE"] = "1"

    def test_returns_false_for_already_deep_capture(self):
        """Recursion guard: if a clip already came from capture-deep
        (clipped_via = 'deep-capture'), don't promote again — capture-deep's
        own output would just trigger another capture-deep run, infinite
        loop."""
        import os as _os
        from process_clip import _should_promote_to_playwright
        _os.environ.pop("ATHENA_DISABLE_PLAYWRIGHT_PROMOTE", None)
        try:
            self.assertFalse(_should_promote_to_playwright(
                "https://linkedin.com/posts/somebody-12345",
                {"clipped_via": "deep-capture"},
            ))
        finally:
            _os.environ["ATHENA_DISABLE_PLAYWRIGHT_PROMOTE"] = "1"

    def test_returns_false_when_no_auth_marker(self):
        """Auth-not-set-up case: don't try to promote without a saved
        Playwright session (capture-deep would just open the login UI
        and block forever in a non-interactive autoingest context)."""
        import os as _os
        from process_clip import _should_promote_to_playwright, _AUTH_MARKER
        _os.environ.pop("ATHENA_DISABLE_PLAYWRIGHT_PROMOTE", None)
        # Test relies on whatever the user's actual auth state is — we just
        # assert: if no marker → False, regardless of URL+clipped_via.
        if not _AUTH_MARKER.exists():
            try:
                self.assertFalse(_should_promote_to_playwright(
                    "https://linkedin.com/posts/somebody-12345",
                    {"clipped_via": "web-clipper"},
                ))
            finally:
                _os.environ["ATHENA_DISABLE_PLAYWRIGHT_PROMOTE"] = "1"
        else:
            # User has a real auth marker; skip this assertion (would
            # trigger the OPPOSITE branch — promoting). The other tests
            # cover the True branches via the env var path.
            _os.environ["ATHENA_DISABLE_PLAYWRIGHT_PROMOTE"] = "1"
            self.skipTest("user has real auth marker — can't test the "
                          "'no marker' branch without removing it")


class TestProcessClipBaitTitleRejection(_VaultCase):
    """0.9.13: process_clip rejects collision-bait Web-Clipper-extracted
    titles ('Post | LinkedIn', 'Feed | LinkedIn', etc.) and derives a
    meaningful title from the stripped body. Symmetric to the 0.9.9
    wiki-side bait-title fix at the raw-write boundary."""

    def _make_clip(self, title: str, source: str, body: str):
        clip_dir = Path(self.vault) / "clippings"
        clip_dir.mkdir(exist_ok=True)
        clip = clip_dir / "test.md"
        clip.write_text(
            f'---\ntitle: "{title}"\nsource: "{source}"\n'
            f'created: 2026-05-10\ntags:\n  - "clippings"\n---\n\n'
            f'# {title}\n\n## Feed post\n\n{body}',
            encoding="utf-8",
        )
        return clip

    def test_post_pipe_linkedin_replaced_by_body_first_sentence(self):
        from process_clip import process_clip
        clip = self._make_clip(
            "Post | LinkedIn",
            "https://www.linkedin.com/posts/somebody_test-7458999",
            "ClaudeBleed is not merely a browser extension flaw. It is another warning.\n\nMore body.",
        )
        path = process_clip(clip, self.vault)
        text = Path(path).read_text(encoding="utf-8")
        self.assertNotIn('title: "Post | LinkedIn"', text)
        self.assertIn("ClaudeBleed", text)

    def test_feed_pipe_linkedin_also_replaced(self):
        from process_clip import process_clip
        clip = self._make_clip(
            "Feed | LinkedIn",
            "https://www.linkedin.com/posts/someone_xyz-7459000",
            "Substantive post body about X. Then more text.",
        )
        path = process_clip(clip, self.vault)
        text = Path(path).read_text(encoding="utf-8")
        self.assertNotIn('title: "Feed | LinkedIn"', text)
        self.assertIn("Substantive post", text)

    def test_legitimate_title_unchanged(self):
        """Sanity: a real article title passes through untouched."""
        from process_clip import process_clip
        clip = self._make_clip(
            "How LangChain Built Agent Memory in 90 Days",
            "https://blog.example.com/agent-memory",
            "Body content.",
        )
        path = process_clip(clip, self.vault)
        text = Path(path).read_text(encoding="utf-8")
        self.assertIn("How LangChain Built", text)

    def test_view_x_profile_pattern_replaced(self):
        """0.10.16: 'View Zhaorun Chen's profile' is LinkedIn profile-link
        chrome — varies per-author so the exact-match _BAIT_CLIP_TITLES
        set can't catch it. Pattern-based detection rejects any 'View
        SOMEONE's profile' title shape."""
        from process_clip import process_clip
        clip = self._make_clip(
            "View Zhaorun Chen's profile",
            "https://www.linkedin.com/posts/zhaorun-chen-1793b6226_x-7458999",
            "AI agents are already going wild but today, we're open-sourcing DTap. Built over 20 months with $120K in API credits.",
        )
        path = process_clip(clip, self.vault)
        text = Path(path).read_text(encoding="utf-8")
        self.assertNotIn('title: "View Zhaorun Chen', text)
        self.assertIn("DTap", text)

    def test_view_x_profile_curly_apostrophe_also_matched(self):
        """The pattern must accept BOTH straight ' and curly ' apostrophes
        — Web Clipper sometimes converts to curly via smart-quotes."""
        from process_clip import _is_bait_title
        self.assertTrue(_is_bait_title("View Praneeta D.’s profile"))  # curly
        self.assertTrue(_is_bait_title("View Praneeta D.'s profile"))      # straight
        self.assertTrue(_is_bait_title("View Brijpandejis profile"))       # no apostrophe

    def test_view_x_profile_pattern_exact_set_still_works(self):
        """Regression: the exact-match set ('Post | LinkedIn', etc.) must
        still get caught after the helper refactor."""
        from process_clip import _is_bait_title
        self.assertTrue(_is_bait_title("Post | LinkedIn"))
        self.assertTrue(_is_bait_title("post | linkedin"))   # case-insensitive
        self.assertTrue(_is_bait_title("Sign Up | LinkedIn"))

    def test_legitimate_view_phrase_not_mangled(self):
        """Conservativity: 'How to view your profile' or 'View page metrics'
        are NOT chrome — they don't end in 's profile / s profile pattern."""
        from process_clip import _is_bait_title
        self.assertFalse(_is_bait_title("How to view your profile metrics"))
        self.assertFalse(_is_bait_title("View page traffic dashboard"))
        self.assertFalse(_is_bait_title("Real Article About Profiles"))

    def test_hashtag_only_title_is_bait(self):
        """A title that is nothing but a row of hashtags is the post's tag
        line grabbed as the <title> — no headline, and an unclickable page
        name. Treat as bait so the body-scan picks the real text. Witnessed
        2026-05-28: itsecuritypartners post titled
        '#agenticai #aisecurity #securityoperations #mdr #dfir'."""
        from process_clip import _is_bait_title
        self.assertTrue(_is_bait_title("#agenticai #aisecurity #securityoperations #mdr #dfir"))
        self.assertTrue(_is_bait_title("#solo"))
        self.assertTrue(_is_bait_title("#a-b #c_d"))   # hyphen/underscore tags
        # Real headlines that merely CONTAIN a hashtag are not bait.
        self.assertFalse(_is_bait_title("Check out #agenticai today"))
        self.assertFalse(_is_bait_title("Why #1 ranking matters for SEO"))


class TestUnicodeTypographyNormalization(unittest.TestCase):
    """0.9.13: math-bold/italic Unicode chars (𝗖𝗹𝗮𝘂𝗱𝗲𝗕𝗹𝗲𝗲𝗱) get folded
    to ASCII (ClaudeBleed) before being used as titles. Without this,
    LinkedIn's bold-emphasis posts produce wiki page filenames with
    Unicode mathematical symbols that are hard to type, search, and
    break in some non-Unicode-aware tools."""

    def test_math_bold_to_ascii(self):
        from process_clip import _normalize_unicode_typography
        # Math bold sans-serif (U+1D5D6 = 𝗖, U+1D5F9 = 𝗹, etc.)
        bold = "\U0001d5d6\U0001d5f9\U0001d5ee\U0001d602\U0001d5f1\U0001d5f2\U0001d5d5\U0001d5f9\U0001d5f2\U0001d5f2\U0001d5f1"
        self.assertEqual(_normalize_unicode_typography(bold), "ClaudeBleed")

    def test_math_italic_to_ascii(self):
        from process_clip import _normalize_unicode_typography
        # Math italic (U+1D44E = 𝑎)
        italic = "\U0001d44e\U0001d44f\U0001d450"  # abc in math italic
        self.assertEqual(_normalize_unicode_typography(italic), "abc")

    def test_ascii_unchanged(self):
        from process_clip import _normalize_unicode_typography
        plain = "Just plain ASCII text."
        self.assertEqual(_normalize_unicode_typography(plain), plain)

    def test_mixed_unicode_and_ascii(self):
        from process_clip import _normalize_unicode_typography
        mixed = "ClaudeBleed \U0001d5f6\U0001d5cc \U0001d5fb\U0001d5fc\U0001d601 merely a flaw."
        normalized = _normalize_unicode_typography(mixed)
        self.assertEqual(normalized, "ClaudeBleed is not merely a flaw.")

    def test_derive_title_normalizes_unicode(self):
        """End-to-end: body with bold-Unicode first sentence → ASCII title."""
        from process_clip import _derive_title_from_body
        body = "\U0001d5d6\U0001d5f9\U0001d5ee\U0001d602\U0001d5f1\U0001d5f2\U0001d5d5\U0001d5f9\U0001d5f2\U0001d5f2\U0001d5f1 is a flaw. More text."
        title = _derive_title_from_body(body)
        self.assertEqual(title, "ClaudeBleed is a flaw.")


class TestSafeUtf8SurrogateSanitization(_VaultCase):
    """1.1.0 release-pass fix: unpaired surrogates (U+D800–U+DFFF) in
    page_content used to crash the wiki write with UnicodeEncodeError
    on Windows. The fix replaces them with U+FFFD at write-time.
    These tests pin the contract:
      1. _safe_utf8 strips lone surrogates and returns a UTF-8-safe string
      2. create_wiki_page tolerates surrogate-bearing llm_result content
         instead of raising
    """

    def test_safe_utf8_strips_lone_surrogate(self):
        from wiki_page import _safe_utf8
        # \udc8d is a lone trailing surrogate (the reported Windows crash byte)
        contaminated = f"clean prefix \udc8d clean suffix"
        result = _safe_utf8(contaminated, label="test")
        # Result must be encodable as UTF-8 without error
        result.encode('utf-8')
        # The surrogate is replaced with U+FFFD
        self.assertIn('�', result)
        self.assertNotIn('\udc8d', result)
        # Clean content around it is preserved
        self.assertIn('clean prefix', result)
        self.assertIn('clean suffix', result)

    def test_safe_utf8_passes_clean_text_unchanged(self):
        from wiki_page import _safe_utf8
        clean = "Hello, world! 你好 — café — Émile"
        self.assertEqual(_safe_utf8(clean, label="test"), clean)

    def test_safe_utf8_handles_empty_and_none(self):
        from wiki_page import _safe_utf8
        self.assertEqual(_safe_utf8("", label="test"), "")
        self.assertIsNone(_safe_utf8(None, label="test"))

    def test_create_wiki_page_tolerates_surrogate_in_summary(self):
        """The user-reported Windows crash path: llm_result.summary
        had a lone surrogate from upstream surrogateescape decoding,
        and the wiki page write crashed at open(..., 'w'). Now the
        write succeeds and the surrogate is replaced with U+FFFD."""
        from wiki_page import create_wiki_page
        url = "https://example.com/surrogate-page"
        raw_dir = Path(self.vault) / "raw" / "webpages" / "artifacts"
        raw_dir.mkdir(parents=True, exist_ok=True)
        raw = raw_dir / "surrogate-page.md"
        raw.write_text(
            f'---\nsource: "{url}"\ntitle: "Surrogate Page"\n---\n\n'
            f'# Surrogate Page\n\nClean body.\n',
            encoding="utf-8",
        )
        # Inject a lone surrogate in the summary — this is what crashes
        # before the fix
        contaminated_summary = f"Summary with bad byte: \udc8d here."
        result = create_wiki_page(
            self.vault, "raw/webpages/artifacts/surrogate-page.md",
            url=url, source='test-surrogate',
            llm_result={
                'title': 'Surrogate Page',
                'summary': contaminated_summary,
                'tags': [],
                'related': [],
                'body': 'Clean body.',
            },
        )
        self.assertEqual(result['status'], 'created', msg=f"unexpected: {result}")
        wiki_text = (Path(self.vault) / result['wiki_path']).read_text(encoding='utf-8')
        # Surrogate replaced; replacement char present
        self.assertNotIn('\udc8d', wiki_text)
        self.assertIn('�', wiki_text)
        # Page contents otherwise intact
        self.assertIn('Surrogate Page', wiki_text)
        self.assertIn('Summary with bad byte', wiki_text)


class TestYouTubeContentCleaning(unittest.TestCase):
    """`_clean_youtube_content` strips the Web Clipper YouTube template's
    comment trees and player-chrome blocks so the LLM summarizer doesn't
    treat commenter quotes as the page's main content.

    Witnessed 2026-05-31: a Pi Agent YouTube capture went through with a
    `## 25 Comments` section nearly the size of the transcript; the wiki
    page summary cited "pinned comment", "per top comment", and "viewer
    comment claims" while the creator's actual description got one line.
    These tests pin the section-strip contract.
    """

    def _body(self, sections):
        """Build a YouTube-shaped body from a list of (heading, content) pairs."""
        out = []
        for h, c in sections:
            out.append(h)
            out.append('')
            out.append(c)
            out.append('')
        return '\n'.join(out)

    def test_strips_single_comments_section(self):
        from wiki_page import _clean_youtube_content
        body = self._body([
            ('## Description', 'The creator wrote this.'),
            ('## Comments', '### @alice\nGreat video!\n### @bob\nAgreed.'),
            ('## Transcript', 'word word word'),
        ])
        cleaned = _clean_youtube_content(body)
        self.assertIn('The creator wrote this.', cleaned)
        self.assertIn('word word word', cleaned)
        self.assertNotIn('@alice', cleaned)
        self.assertNotIn('@bob', cleaned)
        self.assertNotIn('Great video!', cleaned)
        # The `## Comments` heading itself is gone, but `## Description`
        # and `## Transcript` survive intact.
        self.assertIn('## Description', cleaned)
        self.assertIn('## Transcript', cleaned)
        self.assertNotIn('## Comments', cleaned)

    def test_strips_numbered_comments_heading(self):
        from wiki_page import _clean_youtube_content
        body = self._body([
            ('## Description', 'desc'),
            ('## 25 Comments', '### @whoever\npinned reply text'),
            ('## Transcript', 'tx'),
        ])
        cleaned = _clean_youtube_content(body)
        self.assertNotIn('pinned reply text', cleaned)
        self.assertNotIn('@whoever', cleaned)
        self.assertIn('desc', cleaned)
        self.assertIn('tx', cleaned)

    def test_strips_in_this_video_chapter_chrome(self):
        from wiki_page import _clean_youtube_content
        body = self._body([
            ('## Description', 'real description here'),
            ('## In this video', '### Jason Lee\n179K subscribers'),
            ('## Transcript', 'transcript content'),
        ])
        cleaned = _clean_youtube_content(body)
        self.assertNotIn('179K subscribers', cleaned)
        self.assertNotIn('## In this video', cleaned)
        self.assertIn('real description here', cleaned)
        self.assertIn('transcript content', cleaned)

    def test_strips_duplicate_comments_blocks(self):
        # Web Clipper occasionally renders the YouTube page twice (player +
        # full-page view), producing two `## Comments` sections separated
        # by other sections. The stripper must hit every occurrence.
        from wiki_page import _clean_youtube_content
        body = self._body([
            ('## Description', 'desc'),
            ('## 25 Comments', '### @first\nfirst comment text'),
            ('## Chapters', 'ch1\nch2'),
            ('## Comments', '### @second\nsecond comment text'),
            ('## Transcript', 'tx'),
        ])
        cleaned = _clean_youtube_content(body)
        self.assertNotIn('first comment text', cleaned)
        self.assertNotIn('second comment text', cleaned)
        self.assertIn('## Chapters', cleaned)
        self.assertIn('## Transcript', cleaned)

    def test_preserves_body_when_no_noise_sections(self):
        from wiki_page import _clean_youtube_content
        body = self._body([
            ('## Description', 'a clean capture'),
            ('## Transcript', 'just the words'),
        ])
        cleaned = _clean_youtube_content(body)
        # Exact preservation (modulo trailing whitespace) — no false strips.
        self.assertEqual(cleaned.strip(), body.strip())

    def test_preserves_h3_outside_noise_blocks(self):
        # H3 subsections that are NOT inside a `## Comments` block must
        # survive — they may be chapter subtitles, speaker breakdowns, etc.
        from wiki_page import _clean_youtube_content
        body = (
            "## Chapters\n\n"
            "### Intro\nIntro chapter\n\n"
            "### Why It Matters\nMatters chapter\n\n"
            "## Comments\n\n"
            "### @user\nDropped comment\n\n"
            "## Transcript\n\nthe words"
        )
        cleaned = _clean_youtube_content(body)
        self.assertIn('### Intro', cleaned)
        self.assertIn('### Why It Matters', cleaned)
        self.assertNotIn('@user', cleaned)
        self.assertNotIn('Dropped comment', cleaned)

    def test_handles_empty_body(self):
        from wiki_page import _clean_youtube_content
        self.assertEqual(_clean_youtube_content(''), '')
        self.assertEqual(_clean_youtube_content(None), None)

    def test_preprocess_content_invokes_cleaner_for_youtube_urls(self):
        # Integration smoke: preprocess_content() should run the cleaner
        # when the raw frontmatter's source URL is YouTube.
        from wiki_page import preprocess_content
        raw = (
            '---\n'
            'title: "demo"\n'
            'source: "https://www.youtube.com/watch?v=abc123"\n'
            '---\n'
            '## Description\n\nReal description.\n\n'
            '## Comments\n\n### @alice\ncomment payload\n\n'
            '## Transcript\n\nword word'
        )
        out = preprocess_content(raw)
        self.assertNotIn('@alice', out['body'])
        self.assertNotIn('comment payload', out['body'])
        self.assertIn('Real description.', out['body'])

    def test_preprocess_content_skips_cleaner_for_non_youtube_urls(self):
        # Negative control: a non-YouTube URL must NOT trigger the YouTube
        # cleaner — `## Comments` can legitimately appear on other pages
        # (e.g., a GitHub issue threads, blog post comment sections that
        # the user wants to keep).
        from wiki_page import preprocess_content
        raw = (
            '---\n'
            'title: "demo"\n'
            'source: "https://example.com/blog/post"\n'
            '---\n'
            '## Comments\n\nA reader said something useful here.\n'
        )
        out = preprocess_content(raw)
        self.assertIn('A reader said something useful here.', out['body'])


class TestFallbackSummarySkipsHtmlComments(unittest.TestCase):
    """`build_fallback_data` must not pick HTML comments as the page summary.

    Witnessed 2026-05-31: after a fresh arcus YouTube capture where the
    creator left the description blank, the placeholder
    `<!-- No description available... -->` landed verbatim in the
    wiki's `summary:` frontmatter because the paragraph picker passed
    its length+space tests on the comment body. Placeholders are
    derivable-from-context, not editorial content — must skip."""

    def test_skips_html_comment_paragraph(self):
        from wiki_page import build_fallback_data
        body = (
            "# Demo title\n\n"
            "- **Channel/Speaker:** Someone\n"
            "- **Duration:** 12m34s\n\n"
            "## Description\n\n"
            "<!-- No description available (arcus returned no description field, or the video has none). -->\n\n"
            "## Transcript\n\n"
            "This is the real transcript content the picker should land on instead."
        )
        data = build_fallback_data(
            raw_content=body,
            url="https://www.youtube.com/watch?v=abc",
            source_type="video",
            hint_title="Demo title",
        )
        self.assertNotIn("No description available", data["summary"])
        self.assertNotIn("<!--", data["summary"])
        self.assertIn("real transcript content", data["summary"])


class TestBulkRegenSummariesUsesPreprocess(unittest.TestCase):
    """`scripts/bulk-llm-regen-summaries.get_raw_body` must route the raw
    through `preprocess_content` so YouTube comment trees / LinkedIn
    sidebar junk don't reach the LLM via the synthesis-after-create flow.
    Witnessed 2026-05-31: a YouTube wiki created via that path had a
    comment-quoting summary because the script read raws directly,
    bypassing the on-ingest comment-strip applied by wiki_page."""

    def test_youtube_comments_stripped_before_llm(self):
        # Import via importlib because the script has a `-` in its name
        # (importlib accepts the full module spec; plain import doesn't).
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "_bulk_regen",
            Path(__file__).resolve().parent.parent
            / "scripts" / "bulk-llm-regen-summaries.py",
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        with tempfile.TemporaryDirectory() as td:
            vault = Path(td)
            (vault / "raw" / "videos" / "artifacts").mkdir(parents=True)
            raw = vault / "raw" / "videos" / "artifacts" / "video-abc.md"
            raw.write_text(
                "---\n"
                'title: "demo"\n'
                'source: "https://www.youtube.com/watch?v=abc12345DEF"\n'
                "---\n"
                "## Description\n\nReal description text.\n\n"
                "## Comments\n\n### @ALICE\nalice's comment text\n\n"
                "## Transcript\n\nthe words"
            )
            # Point the script's VAULT at our temp vault so its
            # raw_path resolution works.
            mod.VAULT = vault
            body = mod.get_raw_body(
                wiki_path=raw,  # unused on this path
                fm={"raw_path": "raw/videos/artifacts/video-abc.md"},
            )

            self.assertIn("Real description text.", body)
            self.assertIn("the words", body)
            self.assertNotIn("@ALICE", body)
            self.assertNotIn("alice's comment text", body)
            self.assertNotIn("## Comments", body)


class TestSlugYouTubeVideoId(unittest.TestCase):
    """`derive_slug` produces `video-<vid_id>` for YouTube watch / shorts /
    youtu.be URLs so every capture path (Web Clipper, arcus, kb-capture)
    converges on the same slug per video. Without this, the path-only slug
    `youtube-com-watch` collapses every YouTube capture onto one filename
    and the next capture silently overwrites the prior raw. Witnessed
    2026-05-31."""

    def test_youtube_watch_url_uses_video_id_slug(self):
        from slug import derive_slug
        slug = derive_slug(
            "videos",
            "https://www.youtube.com/watch?v=WvuLxxDY37U",
            "Some title",
        )
        self.assertEqual(slug, "video-WvuLxxDY37U")

    def test_youtube_watch_url_with_extra_query_params(self):
        from slug import derive_slug
        slug = derive_slug(
            "videos",
            "https://www.youtube.com/watch?v=jNQXAC9IVRw&t=10s&feature=share",
            None,
        )
        self.assertEqual(slug, "video-jNQXAC9IVRw")

    def test_youtu_be_short_url(self):
        from slug import derive_slug
        slug = derive_slug(
            "videos",
            "https://youtu.be/mNsqiALIoRI",
            "Pi Agent",
        )
        self.assertEqual(slug, "video-mNsqiALIoRI")

    def test_youtube_shorts_url(self):
        from slug import derive_slug
        slug = derive_slug(
            "videos",
            "https://www.youtube.com/shorts/abc12345DEF",
            None,
        )
        self.assertEqual(slug, "video-abc12345DEF")

    def test_youtube_playlist_url_falls_through_to_path_slug(self):
        # Playlist URLs have no single video — must NOT collapse to a
        # `video-<id>` slug. The existing path-based slug logic owns these.
        from slug import derive_slug
        slug = derive_slug(
            "videos",
            "https://www.youtube.com/playlist?list=PLABCDEFG12345",
            None,
        )
        # The path-based slug after url canonicalization (`www.` stripped):
        self.assertEqual(slug, "youtube-com-playlist")
        self.assertFalse(slug.startswith("video-"))

    def test_two_distinct_watch_urls_get_distinct_slugs(self):
        # The whole point of the fix: two YouTube URLs that differ ONLY
        # in the `v=` parameter must produce different slugs. Before the
        # fix, both collapsed to `youtube-com-watch`.
        from slug import derive_slug
        s1 = derive_slug("videos", "https://www.youtube.com/watch?v=AAAAAAAAAAA", None)
        s2 = derive_slug("videos", "https://www.youtube.com/watch?v=BBBBBBBBBBB", None)
        self.assertNotEqual(s1, s2)
        self.assertEqual(s1, "video-AAAAAAAAAAA")
        self.assertEqual(s2, "video-BBBBBBBBBBB")


class TestRawPathCollisionLint(unittest.TestCase):
    """`find_raw_path_collisions` powers `kb lint` §30d — surfaces two-or-
    more wiki pages sharing one raw_path. Permanent guard against any
    future URL family losing its uniqueness in slug derivation; the
    YouTube watch bug (2026-05-31) was fixed at the slug layer but
    other URL families could repeat the pattern."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self._tmp.name)
        (self.vault / "wiki" / "format" / "videos").mkdir(parents=True)

    def tearDown(self):
        self._tmp.cleanup()

    def _write_wiki(self, name: str, raw_path: str) -> None:
        (self.vault / "wiki" / "format" / "videos" / f"{name}.md").write_text(
            "---\n"
            f'title: "{name}"\n'
            f'raw_path: "{raw_path}"\n'
            "---\n\nbody.\n"
        )

    def test_detects_two_wiki_pages_pointing_at_same_raw(self):
        # Reproduces the 2026-05-31 case: two YouTube wiki pages both
        # write `raw_path: raw/videos/artifacts/youtube-com-watch.md`
        # because urlparse stripped the `?v=…` distinguisher.
        from raw_path_collisions import find_raw_path_collisions
        self._write_wiki("YouTube — Pi Agent", "raw/videos/artifacts/youtube-com-watch.md")
        self._write_wiki("YouTube — Jason Lee", "raw/videos/artifacts/youtube-com-watch.md")
        out = find_raw_path_collisions(self.vault)
        self.assertEqual(len(out), 1)
        self.assertIn("raw/videos/artifacts/youtube-com-watch.md", out)
        wikis = sorted(out["raw/videos/artifacts/youtube-com-watch.md"])
        self.assertEqual(len(wikis), 2)
        self.assertIn("wiki/format/videos/YouTube — Pi Agent.md", wikis)
        self.assertIn("wiki/format/videos/YouTube — Jason Lee.md", wikis)

    def test_silent_when_no_collision(self):
        from raw_path_collisions import find_raw_path_collisions
        # Post-fix world: distinct video IDs → distinct slugs → no collisions.
        self._write_wiki("YouTube — A", "raw/videos/artifacts/video-AAAAAAAAAAA.md")
        self._write_wiki("YouTube — B", "raw/videos/artifacts/video-BBBBBBBBBBB.md")
        self.assertEqual(find_raw_path_collisions(self.vault), {})

    def test_three_way_collision_surfaces_all_three_wiki_paths(self):
        from raw_path_collisions import find_raw_path_collisions
        rp = "raw/videos/artifacts/youtube-com-watch.md"
        for n in ("A", "B", "C"):
            self._write_wiki(f"YouTube — {n}", rp)
        out = find_raw_path_collisions(self.vault)
        self.assertEqual(len(out), 1)
        self.assertEqual(len(out[rp]), 3)

    def test_ignores_wiki_pages_without_raw_path(self):
        # Synthesis pages (topics / insights / journal) have no raw_path
        # at all — they're LLM-authored, not source-backed. They must NOT
        # show up in the collision report.
        from raw_path_collisions import find_raw_path_collisions
        (self.vault / "wiki" / "topics").mkdir(parents=True)
        (self.vault / "wiki" / "topics" / "AI Agents.md").write_text(
            '---\ntitle: "AI Agents"\nsource_type: "topic"\n---\n\nsynthesis body.\n'
        )
        self.assertEqual(find_raw_path_collisions(self.vault), {})

    def test_ignores_template_and_contents_files(self):
        from raw_path_collisions import find_raw_path_collisions
        rp = "raw/videos/artifacts/some-raw.md"
        # Two _TEMPLATE.md / _Contents.md scaffolds that happen to carry
        # `raw_path:` placeholders should be skipped even when they collide.
        for name in ("_TEMPLATE", "_Contents"):
            (self.vault / "wiki" / "format" / "videos" / f"{name}.md").write_text(
                f'---\ntitle: "{name}"\nraw_path: "{rp}"\n---\n'
            )
        self.assertEqual(find_raw_path_collisions(self.vault), {})


class TestArcusVideoBodyDescription(unittest.TestCase):
    """`arcus_video._build_athena_body` emits the creator's description into
    the `## Description` section when arcus exposes it (via SourceMetadata
    .description, populated by arcus >= the description-support release),
    and falls back to the placeholder comment when absent. Witnessed
    2026-05-31: every kb-add-via-arcus YouTube raw shipped with the
    placeholder because arcus didn't expose info['description']."""

    def test_description_section_uses_metadata_value_when_present(self):
        from arcus_video import _build_athena_body
        body = _build_athena_body(
            "transcript line one.",
            channel="A Channel",
            duration_str="3m21s",
            date_str="2026-05-31",
            video_description="A useful description the creator wrote.",
        )
        self.assertIn("## Description", body)
        self.assertIn("A useful description the creator wrote.", body)
        # And the placeholder comment is NOT present once we have real text.
        self.assertNotIn("No description available", body)

    def test_description_section_falls_back_to_placeholder_when_absent(self):
        from arcus_video import _build_athena_body
        body = _build_athena_body(
            "transcript line one.",
            channel="A Channel",
            duration_str="3m21s",
            date_str="2026-05-31",
            video_description="",  # arcus returned no description
        )
        self.assertIn("## Description", body)
        self.assertIn("No description available", body)

    def test_description_whitespace_only_treated_as_absent(self):
        # Some videos have a description that's just whitespace / a single
        # newline. Treat that as "no description" — emitting blank into
        # the wiki page is worse than the placeholder comment.
        from arcus_video import _build_athena_body
        body = _build_athena_body(
            "tx",
            channel="Ch",
            duration_str="1m",
            date_str="2026-05-31",
            video_description="   \n  \n",
        )
        self.assertIn("No description available", body)


if __name__ == "__main__":
    unittest.main()
