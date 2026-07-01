"""capture-deep navigates the ORIGINAL clipped URL, not the synthetic canonical.

Anchor (2026-07-01): a LinkedIn share post
  https://www.linkedin.com/posts/emilyhartstone_..-share-7475613415152005121-X58e/
was clipped and never added. canonicalize() collapses the share/activity/ugcPost
URL forms to a synthetic /posts/ugcpost-<id> dedup key, and capture-deep was
navigating THAT — but LinkedIn rejects it ("Invalid post link") because a `share`
URN id in the `ugcpost-` slot is a different namespace. The browser must navigate
the exact URL the user's browser resolved (the original clip `source:`), keeping
the canonical form for dedup only.

Also covers Fix B: process_clip's CLI treats a vanished clip as a benign no-op
(exit 0), so concurrent watchers don't toast "clip not found" for a clip another
pass already handled.

Run from vault root:
    python3 -m pytest tests/test_capture_deep_url.py -v
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LIB = ROOT / "bin" / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

import process_clip as pc  # type: ignore  # noqa: E402
import unified_ingest as ui  # type: ignore  # noqa: E402
from url_canonical import canonicalize, is_unservable_canonical  # type: ignore  # noqa: E402

SHARE_URL = (
    "https://www.linkedin.com/posts/emilyhartstone_agenticai-aigovernance-"
    "runtimeauthority-share-7475613415152005121-X58e/"
    "?utm_source=social_share_send&utm_medium=ios_app"
)
CANON = canonicalize(SHARE_URL).url  # https://linkedin.com/posts/ugcpost-7475613415152005121


class CaptureDeepBest(unittest.TestCase):
    def setUp(self):
        self._orig_run = pc._run_capture_deep
        self.addCleanup(setattr, pc, "_run_capture_deep", self._orig_run)

    def test_canonical_collapses_share_to_synthetic_ugcpost(self):
        # Guard the premise: the share URL canonicalizes to a URL LinkedIn
        # does not serve. If this ever stops being true, the fix's rationale
        # changed and this whole test should be revisited.
        self.assertEqual(CANON, "https://linkedin.com/posts/ugcpost-7475613415152005121")
        self.assertNotEqual(CANON, SHARE_URL)

    def test_navigates_original_url_first_no_fallback_on_success(self):
        calls = []

        def fake_run(url, vault):
            calls.append(url)
            return Path("/tmp/deep.md")  # success

        pc._run_capture_deep = fake_run
        out = pc._capture_deep_best(SHARE_URL, CANON, Path("/vault"))
        self.assertEqual(out, Path("/tmp/deep.md"))
        # Only the original URL is tried; the synthetic canonical is never hit.
        self.assertEqual(calls, [SHARE_URL])

    def test_falls_back_to_canonical_only_when_original_yields_nothing(self):
        calls = []

        def fake_run(url, vault):
            calls.append(url)
            return None if url == SHARE_URL else Path("/tmp/deep.md")

        pc._run_capture_deep = fake_run
        out = pc._capture_deep_best(SHARE_URL, CANON, Path("/vault"))
        self.assertEqual(out, Path("/tmp/deep.md"))
        self.assertEqual(calls, [SHARE_URL, CANON])

    def test_dedups_when_original_equals_canonical(self):
        calls = []

        def fake_run(url, vault):
            calls.append(url)
            return None

        pc._run_capture_deep = fake_run
        out = pc._capture_deep_best(CANON, CANON, Path("/vault"))
        self.assertIsNone(out)
        self.assertEqual(calls, [CANON])  # tried once, not twice


class ProcessClipPromotePassesOriginal(unittest.TestCase):
    """The promote branch in process_clip() hands _capture_deep_best the
    original clip URL as the navigation target."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kb-deep-url-"))
        (self.tmp / "clippings").mkdir(parents=True)
        self.clip = self.tmp / "clippings" / "linkedin.md"
        self.clip.write_text(
            "---\n"
            'title: "Post | LinkedIn"\n'
            f'source: "{SHARE_URL}"\n'
            'clipped_via: "web-clipper"\n'
            "---\n\n"
            "Some real post body text so the clip is not empty.\n",
            encoding="utf-8",
        )
        self._saved = {
            "promote": pc._should_promote_to_playwright,
            "best": pc._capture_deep_best,
            "ingest": ui.ingest,
            "touch": pc._touch_wiki_last_updated_for_url,
        }
        pc._should_promote_to_playwright = lambda canonical, fm: True
        pc._touch_wiki_last_updated_for_url = lambda *a, **k: None
        self.addCleanup(self._restore)

    def _restore(self):
        pc._should_promote_to_playwright = self._saved["promote"]
        pc._capture_deep_best = self._saved["best"]
        ui.ingest = self._saved["ingest"]
        pc._touch_wiki_last_updated_for_url = self._saved["touch"]
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_promote_hands_original_url_as_nav_target(self):
        recorded = {}

        def fake_best(clipped_url, canonical_url, vault_root):
            recorded["clipped"] = clipped_url
            recorded["canonical"] = canonical_url
            return None  # simulate capture-deep unavailable → fall through

        fake_raw = self.tmp / "raw/webpages/linkedin.md"
        fake_raw.parent.mkdir(parents=True, exist_ok=True)
        fake_raw.write_text("ok", encoding="utf-8")

        def fake_ingest(inp):
            return ui.IngestResult(
                raw_path=fake_raw, source_type="webpage", canonical_url=inp.url,
                title="Post", extracted_via="clip", was_re_routed=False,
            )

        pc._capture_deep_best = fake_best
        ui.ingest = fake_ingest

        pc.process_clip(self.clip, self.tmp)
        # The navigation target is the original share URL, NOT the synthetic
        # /posts/ugcpost-<id> canonical.
        self.assertEqual(recorded["clipped"], SHARE_URL)
        self.assertIn("ugcpost", recorded["canonical"])


class UnservableCanonical(unittest.TestCase):
    """The stored `source:` link must stay resolvable even when the canonical
    dedup key isn't a URL LinkedIn serves."""

    def test_synthetic_forms_are_unservable(self):
        for u in (
            "https://linkedin.com/posts/ugcpost-123",
            "https://linkedin.com/posts/share-123",
            "https://linkedin.com/posts/activity-123",
            "https://www.linkedin.com/posts/ugcpost-123/",
        ):
            self.assertTrue(is_unservable_canonical(u), u)
        # The canonical of the anchor share URL is unservable.
        self.assertTrue(is_unservable_canonical(CANON))

    def test_real_and_foreign_urls_are_servable(self):
        for u in (
            "https://linkedin.com/posts/emilyhartstone_x-share-123-X58e",
            "https://linkedin.com/pulse/some-article",
            "https://github.com/a/b",
            "https://example.com/x",
            "",
        ):
            self.assertFalse(is_unservable_canonical(u), u)


class WriteRawSourceOverride(unittest.TestCase):
    """write_raw(source_url_override=…) changes only the `source:` link; the
    slug/filename stays canonical so dedup is unaffected."""

    def test_override_changes_source_not_slug(self):
        from raw_writer import write_raw  # type: ignore
        tmp = Path(tempfile.mkdtemp(prefix="kb-src-override-"))
        (tmp / "CLAUDE.md").write_text("", encoding="utf-8")
        self.addCleanup(lambda: __import__("shutil").rmtree(tmp, ignore_errors=True))
        original = "https://www.linkedin.com/posts/emilyhartstone_x-share-7475613415152005121-X58e"
        p = write_raw(
            vault_root=tmp,
            source_type="webpage",
            url=CANON,  # canonical synthetic form → slug basis
            title="Post",
            body="Real body text.",
            canonicalize_url=False,
            source_url_override=original,
        )
        # Filename/slug still derived from the canonical identity.
        self.assertIn("ugcpost-7475613415152005121", p.name)
        text = p.read_text(encoding="utf-8")
        # The clickable source is the original resolvable URL, not the synthetic.
        self.assertIn(f'source: "{original}"', text)
        self.assertNotIn('source: "https://linkedin.com/posts/ugcpost-', text)


class ReclipSyncsWikiSourceLink(unittest.TestCase):
    """A re-clip that corrected the raw `source:` heals the existing wiki's
    Source link in place (no manual refresh, no LLM re-synthesis)."""

    RESOLVABLE = "https://www.linkedin.com/posts/emilyhartstone_x-share-7475613415152005121-X58e"

    def _wiki(self, url: str, raw_rel: str) -> str:
        return (
            "---\n"
            'title: "A post"\n'
            'source_type: "webpage"\n'
            f'raw_path: "{raw_rel}"\n'
            f'url: "{url}"\n'
            "date_added: 2026-06-01\n"
            "tags: [webpage]\n"
            "---\n"
            f"![[raw/favicons/linkedin.com.png|16]] [Source]({url}) · "
            f"[[{raw_rel}|Local Copy]]\n\n## Key Findings\n\n- x\n"
        )

    def test_sync_helper_rewrites_url_and_source_when_diverged(self):
        tmp = Path(tempfile.mkdtemp(prefix="kb-sync-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(tmp, ignore_errors=True))
        raw_rel = "raw/webpages/artifacts/linkedin-com-posts-ugcpost-7475613415152005121.md"
        (tmp / "raw/webpages/artifacts").mkdir(parents=True)
        (tmp / raw_rel).write_text(
            f'---\ntitle: "A post"\nsource: "{self.RESOLVABLE}"\n---\n\nBody.\n',
            encoding="utf-8",
        )
        stale = self._wiki(CANON, raw_rel)  # wiki still holds the synthetic dead link
        synced = pc._sync_wiki_source_link(stale, tmp)
        self.assertIn(f'url: "{self.RESOLVABLE}"', synced)
        self.assertIn(f"[Source]({self.RESOLVABLE})", synced)
        self.assertNotIn("ugcpost-7475613415152005121\"", synced)  # no dead url left
        # Idempotent: a second pass is a no-op.
        self.assertEqual(pc._sync_wiki_source_link(synced, tmp), synced)

    def test_touch_after_reclip_heals_the_link(self):
        tmp = Path(tempfile.mkdtemp(prefix="kb-touch-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(tmp, ignore_errors=True))
        raw_rel = "raw/webpages/artifacts/linkedin-com-posts-ugcpost-7475613415152005121.md"
        (tmp / "raw/webpages/artifacts").mkdir(parents=True)
        (tmp / "wiki/format/webpages").mkdir(parents=True)
        (tmp / "inbox").mkdir(parents=True)
        # Raw already re-clipped: source is the resolvable original.
        (tmp / raw_rel).write_text(
            f'---\ntitle: "A post"\nsource: "{self.RESOLVABLE}"\n---\n\nBody.\n',
            encoding="utf-8",
        )
        # Existing wiki page still holds the synthetic dead link.
        page = "A post"
        (tmp / "wiki/format/webpages" / f"{page}.md").write_text(
            self._wiki(CANON, raw_rel), encoding="utf-8"
        )
        # url-resolved.tsv maps the canonical to the page (touch looks up by url).
        (tmp / "inbox" / "url-resolved.tsv").write_text(
            f"captured\t{page}\t{CANON}\t2026-06-01T00:00:00\n", encoding="utf-8"
        )
        pc._touch_wiki_last_updated_for_url(tmp, CANON)
        healed = (tmp / "wiki/format/webpages" / f"{page}.md").read_text(encoding="utf-8")
        self.assertIn(f'url: "{self.RESOLVABLE}"', healed)
        self.assertIn(f"[Source]({self.RESOLVABLE})", healed)
        self.assertNotIn("posts/ugcpost-7475613415152005121", healed)


class CliMissingClipIsBenign(unittest.TestCase):
    """Fix B: a vanished clip is not a failure — exit 0, no error toast."""

    def test_cli_returns_zero_for_missing_clip(self):
        saved = sys.argv
        try:
            sys.argv = ["process_clip.py", "/nonexistent/vault", "/nonexistent/clip.md"]
            rc = pc._cli()
        finally:
            sys.argv = saved
        self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
