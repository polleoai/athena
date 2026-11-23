"""Parity tests for the unified_ingest migration (task #229).

The 2026-05 refactor moved the Web-Clipper body-processing pipeline out of
`process_clip.process_clip()` and into
`unified_ingest._handle_webpage_from_markdown_body()`. The six transforms
(LinkedIn chrome strip → bait-title fallback → twimg rewrite → blob-video
strip → trailing-ellipsis strip → referenced-URL queue) were NOT rewritten —
they stayed as shared helpers in `process_clip.py`; only the orchestration
moved. `process_clip()` now delegates to `unified_ingest.ingest()`.

This file proves the migration preserved behavior, two ways:

  1. TestOldVsNewEquivalence — loads the literal pre-migration `process_clip`
     from tests/fixtures/process_clip_pre_migration.py (a verbatim, sha256-
     pinned copy of the git blob) and runs BOTH the old and new entry point over the
     SAME clip fixtures in isolated temp vaults, then asserts the produced
     raw artifact is equivalent (body byte-identical; frontmatter identical
     modulo volatile date fields; same URLs auto-queued). This is the
     direct "functions the same as before the refactor" guard. It runs in
     every tree, git checkout or not, and never skips.

     One deliberate frontmatter exception: the post-migration 1.6.2
     resolvable-Source fix records the resolvable ORIGINAL url (not the
     synthetic canonical) for an unservable-canonical clip (LinkedIn
     /posts/{ugcpost,activity,share}-<id>). The LinkedIn fixture asserts that
     intended `source` divergence explicitly instead of demanding old==new.

  2. TestWebpagePipelineCharacterization — durable property assertions on the
     CURRENT path for each transform, so a future edit to the orchestration
     that breaks one transform fails loudly with a readable message, not
     only as an old-vs-new diff.

Run from vault root:
    python3 -m pytest tests/test_pipeline_parity.py -v
    python3 -m unittest tests.test_pipeline_parity -v
"""

from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path

# Add bin/lib to import path (matches how kb dispatches).
_VAULT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_VAULT / "bin" / "lib"))

from raw_parser import read_raw_frontmatter  # noqa: E402
import process_clip as process_clip_new  # noqa: E402  (HEAD: delegates to unified_ingest)


# Frontmatter keys whose values are wall-clock dates — identical across two
# runs on the same day, but excluded from comparison so the suite never goes
# red at a midnight boundary.
_VOLATILE_FM_KEYS = frozenset({
    "date_added", "captured", "captured_at", "ingested_at",
    "last_updated", "date", "created",
})


# ─────────────────────────────────────────────────────────────────────
# Clip fixtures — each exercises a distinct subset of the six transforms.
# Web-Clipper-shaped: YAML frontmatter (title/source/clipped_via) + body.
# ─────────────────────────────────────────────────────────────────────

# LinkedIn: logged-in chrome (profile sidebar + Connect button) above the
# post, comments footer below, a post image concatenated to trailing chrome,
# a "link in comments" github URL, and the collision-bait title "Post |
# LinkedIn". Exercises: chrome strip + image re-attach + bait-title fallback
# + URL queue.
_LINKEDIN_CLIP = {
    "title": "Post | LinkedIn",
    "source": "https://www.linkedin.com/posts/janedoe_claudebleed-activity-7300000000000000000-abcd",
    "clipped_via": "web-clipper",
    "body": (
        "## Feed post\n"
        "View Jane Doe's profile\n"
        "Jane Doe\n"
        "CS PhD Student at The University of Chicago\n"
        "2h\n"
        "Connect\n"
        "\n"
        "We just shipped ClaudeBleed, an open-source tool for detecting prompt "
        "injection in agent harnesses. Try it and tell me what breaks — repo link "
        "in the comments below.\n"
        "\n"
        "![Post image](https://media.licdn.com/dms/image/v2/feedshare-abc123)\n"
        "\n"
        "Repo: https://github.com/janedoe/claudebleed\n"
        "\n"
        "## Most relevant\n"
        "Top comment from someone\n"
        "42 comments\n"
    ),
}

# X.com: a large twimg image, a blob: video overlay, and a display-truncated
# github URL ending in an ellipsis, under the bait title "Home | X".
# Exercises: twimg rewrite + blob-video strip + ellipsis strip + URL queue +
# bait-title fallback. (No LinkedIn chrome strip — not a linkedin.com URL.)
_XCOM_CLIP = {
    "title": "Home | X",
    "source": "https://x.com/someone/status/1790000000000000000",
    "clipped_via": "web-clipper",
    "body": (
        "Most agent failures are harness failures, not model failures.\n"
        "\n"
        "![chart](https://pbs.twimg.com/media/Gabc123?format=jpg&name=large)\n"
        "\n"
        '<video controls><source src="blob:https://x.com/9f8e7d6c" type="video/mp4"></video>\n'
        "\n"
        "Full writeup: https://github.com/someone/agent-harness…\n"
    ),
}

# Generic Substack-style article: real (non-bait) title, clean body, an
# arxiv reference in prose. Exercises the pure pass-through case + arxiv
# URL auto-queue, with NO chrome strip / image rewrite / title change.
_GENERIC_CLIP = {
    "title": "Why Harnesses Eat Models",
    "source": "https://example.substack.com/p/why-harnesses-eat-models",
    "clipped_via": "web-clipper",
    "body": (
        "This is a thoughtful essay about agent architectures and where the "
        "real engineering leverage sits.\n"
        "\n"
        "It builds on a recent paper, https://arxiv.org/abs/2501.12345, and "
        "argues the harness is the product.\n"
    ),
}

_ALL_FIXTURES = {
    "linkedin": _LINKEDIN_CLIP,
    "xcom": _XCOM_CLIP,
    "generic": _GENERIC_CLIP,
}


def _write_clip(vault: Path, fixture: dict) -> Path:
    """Materialize a Web-Clipper-shaped clip file inside vault/clippings/."""
    clippings = vault / "clippings"
    clippings.mkdir(parents=True, exist_ok=True)
    clip = clippings / "clip.md"
    fm_lines = ["---"]
    for key in ("title", "source", "clipped_via"):
        fm_lines.append(f'{key}: "{fixture[key]}"')
    fm_lines.append("---")
    clip.write_text("\n".join(fm_lines) + "\n\n" + fixture["body"], encoding="utf-8")
    return clip


def _read_queued_urls(vault: Path) -> list[str]:
    """Return the URLs auto-queued to inbox/url-new.txt (sorted), or []."""
    f = vault / "inbox" / "url-new.txt"
    if not f.exists():
        return []
    return sorted(
        line.strip()
        for line in f.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    )


def _frontmatter_without_dates(raw_path: Path) -> dict:
    fm, _ = read_raw_frontmatter(raw_path)
    return {k: v for k, v in (fm or {}).items() if k not in _VOLATILE_FM_KEYS}


def _body_of(raw_path: Path) -> str:
    _, body = read_raw_frontmatter(raw_path)
    return body


# The literal pre-migration process_clip: `git show a44a03ce^:Projects/athena/
# bin/lib/process_clip.py`, the parent of the commit that delegated it to
# unified_ingest (2026-05-24), committed verbatim as a fixture. It used to be
# read from git history at test time, which made the result depend on the
# repo the test ran in. Every dev clone resolved this blob and passed, but a
# tree with other history (the public release tree) resolved a different
# "pre-migration" version and failed: the 3 TestOldVsNewEquivalence entries
# in tests/known-failures.txt (#239). The sha256 pins the fixture so an edit
# to it can't pass silently.
_OLD_PROCESS_CLIP = Path(__file__).resolve().parent / "fixtures" / "process_clip_pre_migration.py"
_OLD_PROCESS_CLIP_SHA256 = "17391d17910b8bf54b4834c24f7507919e47dbb38d197ec2a7e1ca7e9dfe1f7e"


def _load_old_process_clip():
    """Import the pre-migration process_clip fixture as `process_clip_old`.
    Raises if the fixture is missing or altered: a skip here would hide a
    broken parity guard."""
    import hashlib
    data = _OLD_PROCESS_CLIP.read_bytes()
    got = hashlib.sha256(data).hexdigest()
    if got != _OLD_PROCESS_CLIP_SHA256:
        raise AssertionError(f"{_OLD_PROCESS_CLIP.name} changed: sha256 {got}")
    spec = importlib.util.spec_from_file_location("process_clip_old", _OLD_PROCESS_CLIP)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # imports raw_writer/url_canonical from bin/lib
    return module


# ─────────────────────────────────────────────────────────────────────
# 1. Direct old-vs-new equivalence — the "same as before the refactor" guard.
# ─────────────────────────────────────────────────────────────────────


class TestOldVsNewEquivalence(unittest.TestCase):
    """Run the literal pre-migration process_clip and the HEAD process_clip
    over identical clip fixtures; assert equivalent raw output.

    Equivalence = body byte-identical AND non-date frontmatter identical AND
    same URLs auto-queued. (The two write_raw calls are identical by code, so
    in practice the whole file matches; we exclude volatile date fields to
    stay robust across a midnight boundary.)
    """

    @classmethod
    def setUpClass(cls):
        cls.old = _load_old_process_clip()

    def setUp(self):
        # Disable LinkedIn → capture-deep auto-promote so both paths process
        # the Web-Clipper body directly (no subprocess / network).
        self._saved_env = {
            k: os.environ.get(k)
            for k in ("ATHENA_DISABLE_PLAYWRIGHT_PROMOTE", "KB_DISABLE_AUTO_PROMOTE_DEEP")
        }
        os.environ["ATHENA_DISABLE_PLAYWRIGHT_PROMOTE"] = "1"
        os.environ["KB_DISABLE_AUTO_PROMOTE_DEEP"] = "1"

    def tearDown(self):
        for k, v in getattr(self, "_saved_env", {}).items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _run_both(self, fixture: dict):
        """Process the fixture through old + new in isolated temp vaults.
        Returns (old_raw_path, new_raw_path, old_vault, new_vault)."""
        old_dir = tempfile.TemporaryDirectory()
        new_dir = tempfile.TemporaryDirectory()
        self.addCleanup(old_dir.cleanup)
        self.addCleanup(new_dir.cleanup)
        old_vault, new_vault = Path(old_dir.name), Path(new_dir.name)

        old_raw = self.old.process_clip(_write_clip(old_vault, fixture), old_vault)
        new_raw = process_clip_new.process_clip(_write_clip(new_vault, fixture), new_vault)
        return Path(old_raw), Path(new_raw), old_vault, new_vault

    def _assert_equivalent(self, fixture_name: str, source_may_differ: bool = False):
        fixture = _ALL_FIXTURES[fixture_name]
        old_raw, new_raw, old_vault, new_vault = self._run_both(fixture)

        self.assertTrue(old_raw.exists(), f"{fixture_name}: old produced no raw")
        self.assertTrue(new_raw.exists(), f"{fixture_name}: new produced no raw")

        # Both land in the same category subtree.
        self.assertIn("raw/webpages/", str(old_raw))
        self.assertIn("raw/webpages/", str(new_raw))
        # Same filename (slug derived from the same title).
        self.assertEqual(old_raw.name, new_raw.name,
                         f"{fixture_name}: slug/title diverged")

        # The parity surface: the transformed body must be byte-identical.
        self.assertEqual(_body_of(old_raw), _body_of(new_raw),
                         f"{fixture_name}: body diverged after migration")

        old_fm = _frontmatter_without_dates(old_raw)
        new_fm = _frontmatter_without_dates(new_raw)

        if source_may_differ:
            # The 1.6.2 resolvable-Source fix landed AFTER the 2026-05 migration
            # and intentionally changed source recording: for an unservable
            # canonical (LinkedIn /posts/{ugcpost,activity,share}-<id>) the new
            # path records the resolvable ORIGINAL url, where the pre-migration
            # path stored the synthetic canonical. That divergence is the fix,
            # not a migration regression — so compare the rest of the
            # frontmatter and pin the new `source` to the resolvable original.
            old_src = old_fm.pop("source", None)
            new_src = new_fm.pop("source", None)
            self.assertEqual(
                new_src, fixture["source"].split("?", 1)[0],
                f"{fixture_name}: new pipeline must record the resolvable original Source")
            self.assertNotEqual(
                new_src, old_src,
                f"{fixture_name}: expected the 1.6.2 Source divergence from the "
                f"pre-migration synthetic canonical")

        # Remaining (or all) non-date frontmatter must match (title fallback,
        # clipped_via, url…).
        self.assertEqual(old_fm, new_fm,
                         f"{fixture_name}: frontmatter diverged after migration")

        # Same referenced URLs auto-queued.
        self.assertEqual(_read_queued_urls(old_vault), _read_queued_urls(new_vault),
                         f"{fixture_name}: auto-queued URLs diverged")

    def test_linkedin_clip_equivalent(self):
        # source_may_differ: LinkedIn is an unservable canonical, so the 1.6.2
        # fix deliberately records a different (resolvable) Source than the
        # pre-migration path. See _assert_equivalent.
        self._assert_equivalent("linkedin", source_may_differ=True)

    def test_xcom_clip_equivalent(self):
        self._assert_equivalent("xcom")

    def test_generic_clip_equivalent(self):
        self._assert_equivalent("generic")


# ─────────────────────────────────────────────────────────────────────
# 2. Characterization — durable property assertions on the CURRENT path.
#    These hold even when the git-blob comparison is unavailable.
# ─────────────────────────────────────────────────────────────────────


class TestWebpagePipelineCharacterization(unittest.TestCase):
    """Assert each of the six transforms fires through the current
    process_clip → unified_ingest path. Property-based (not exact-body) so
    they read as a spec of what the webpage pipeline guarantees."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self._tmp.name)
        self._saved_env = {
            k: os.environ.get(k)
            for k in ("ATHENA_DISABLE_PLAYWRIGHT_PROMOTE", "KB_DISABLE_AUTO_PROMOTE_DEEP")
        }
        os.environ["ATHENA_DISABLE_PLAYWRIGHT_PROMOTE"] = "1"
        os.environ["KB_DISABLE_AUTO_PROMOTE_DEEP"] = "1"

    def tearDown(self):
        for k, v in self._saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self._tmp.cleanup()

    def _process(self, fixture: dict):
        raw = process_clip_new.process_clip(_write_clip(self.vault, fixture), self.vault)
        return Path(raw)

    def test_linkedin_chrome_stripped_and_title_derived(self):
        raw = self._process(_LINKEDIN_CLIP)
        fm, body = read_raw_frontmatter(raw)

        # Leading chrome gone: the user's profile sidebar + Connect button.
        self.assertNotIn("View Jane Doe's profile", body)
        self.assertNotIn("\nConnect\n", body)
        self.assertNotIn("CS PhD Student", body)
        # Trailing comments footer gone.
        self.assertNotIn("Most relevant", body)
        self.assertNotIn("42 comments", body)
        # Actual post content survives.
        self.assertIn("We just shipped ClaudeBleed", body)
        # Post image re-attached (survives the trailing-chrome cut).
        self.assertIn("feedshare-abc123", body)
        # Bait title "Post | LinkedIn" replaced by a body-derived title.
        self.assertNotEqual(fm.get("title", "").strip().lower(), "post | linkedin")
        self.assertTrue(fm.get("title"))

    def test_linkedin_referenced_github_url_queued(self):
        self._process(_LINKEDIN_CLIP)
        queued = _read_queued_urls(self.vault)
        self.assertIn("https://github.com/janedoe/claudebleed", queued)

    def test_xcom_twimg_downsized_and_width_constrained(self):
        raw = self._process(_XCOM_CLIP)
        _, body = read_raw_frontmatter(raw)
        # large → medium, and a 600px width constraint added.
        self.assertNotIn("name=large", body)
        self.assertIn("name=medium", body)
        self.assertIn('width="600"', body)

    def test_xcom_blob_video_replaced_with_source_link(self):
        raw = self._process(_XCOM_CLIP)
        _, body = read_raw_frontmatter(raw)
        self.assertNotIn("blob:", body)
        self.assertIn("Watch video on source", body)

    def test_xcom_trailing_ellipsis_stripped_from_url(self):
        raw = self._process(_XCOM_CLIP)
        _, body = read_raw_frontmatter(raw)
        self.assertNotIn("…", body)  # no stray ellipsis left in any URL
        self.assertIn("https://github.com/someone/agent-harness", body)

    def test_generic_passthrough_preserves_real_title_and_queues_arxiv(self):
        raw = self._process(_GENERIC_CLIP)
        fm, body = read_raw_frontmatter(raw)
        # Non-bait title preserved verbatim.
        self.assertEqual(fm.get("title"), "Why Harnesses Eat Models")
        # Body content intact (pure pass-through; nothing stripped).
        self.assertIn("the harness is the product", body)
        # arxiv reference auto-queued for separate capture.
        self.assertIn("https://arxiv.org/abs/2501.12345", _read_queued_urls(self.vault))


if __name__ == "__main__":
    unittest.main()
