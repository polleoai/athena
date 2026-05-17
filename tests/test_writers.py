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
        from process_clip import process_clip
        clip = self._write_clip("gh.md", "https://github.com/foo/bar")
        path = process_clip(clip, self.vault)
        self.assertEqual(Path(path).relative_to(self.vault).parts[:3],
                         ("raw", "repos", "artifacts"))

    def test_youtube_clip_routes_to_videos(self):
        from process_clip import process_clip
        clip = self._write_clip("yt.md", "https://www.youtube.com/watch?v=abc123")
        path = process_clip(clip, self.vault)
        self.assertEqual(Path(path).relative_to(self.vault).parts[:3],
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

    def test_empty_body_rejected(self):
        from process_clip import process_clip, ProcessClipError
        clip_dir = Path(self.vault) / "clippings"
        clip_dir.mkdir(exist_ok=True)
        clip = clip_dir / "empty.md"
        # Truly empty body — no H1, no content after FM closing `---`
        clip.write_text(
            '---\ntitle: "x"\nsource: "https://example.com"\n---\n',
            encoding="utf-8",
        )
        with self.assertRaises(ProcessClipError):
            process_clip(clip, self.vault)


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
        the existing wiki page gets rewritten, not skipped."""
        from wiki_page import create_wiki_page
        url = "https://example.com/refresh-target"
        raw_path = self._seed_raw("refresh-target", "Refresh Target", "First-pass body content.", url)
        # First create — normal flow with an llm_result (schema requires summary)
        first = create_wiki_page(
            self.vault, raw_path, url=url, source='test-seed',
            llm_result=self._make_llm_result("Refresh Target", "First-pass body content."),
        )
        self.assertEqual(first['status'], 'created', msg=f"unexpected: {first}")
        first_path = Path(self.vault) / first['wiki_path']
        first_size = first_path.stat().st_size
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
            llm_result=self._make_llm_result("Refresh Target", bigger_body),
        )
        self.assertEqual(second['status'], 'created')
        # Same wiki path (filename preserved)
        self.assertEqual(second['wiki_path'], first['wiki_path'])
        # Body changed (size delta > 0)
        new_size = first_path.stat().st_size
        self.assertGreater(new_size, first_size)
        # New body content is in the rewritten page
        self.assertIn("Second-pass body", first_path.read_text(encoding='utf-8'))

    def test_overwrite_true_snapshots_to_trash(self):
        """Refresh must leave a recoverable copy in .kb-trash/ — the
        atomic write would otherwise destroy the prior body. This is
        the recovery contract for `kb undo`."""
        from wiki_page import create_wiki_page
        url = "https://example.com/snapshot-test"
        raw_path = self._seed_raw("snap", "Snap", "Original body text.", url)
        create_wiki_page(
            self.vault, raw_path, url=url, source='test-seed',
            llm_result=self._make_llm_result("Snap", "Original body text."),
        )
        # Refresh
        Path(self.vault, raw_path).write_text(
            f'---\nsource: "{url}"\ntitle: "Snap"\n---\n\n# Snap\n\nNew body text.\n',
            encoding="utf-8",
        )
        create_wiki_page(
            self.vault, raw_path, url=url, source='test-refresh', overwrite=True,
            llm_result=self._make_llm_result("Snap", "New body text."),
        )
        # Find the snapshot
        trash_root = Path(self.vault) / ".kb-trash"
        self.assertTrue(trash_root.exists(), "no .kb-trash directory created")
        snapshot_dirs = list(trash_root.glob("*_refresh_wiki"))
        self.assertEqual(len(snapshot_dirs), 1, f"expected 1 snapshot dir, got {snapshot_dirs}")
        snapshot_files = list(snapshot_dirs[0].glob("*.md"))
        self.assertEqual(len(snapshot_files), 1)
        # Snapshot has the ORIGINAL content
        self.assertIn("Original body text", snapshot_files[0].read_text(encoding='utf-8'))

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


if __name__ == "__main__":
    unittest.main()
