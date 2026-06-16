"""Regression: wiki category derivation must be separator-agnostic.

Witnessed on the Windows VM matrix (argus all-SSH net run, AT-4): a youtube
video was captured to `raw\\videos\\artifacts\\video-X.md` (native Windows
backslashes), but `create_wiki_page` derived `source_type` by matching the
forward-slash literal `/videos/` against that path — which fails on backslash
paths — so the video wiki was written to `wiki/format/webpages/` instead of
`wiki/format/videos/`, and the test's `wiki/format/videos/` check found nothing.

These tests pin `_source_type_and_subdir_from_raw_path` to normalize separators
so posix `/videos/` and Windows `\\videos\\` both resolve to source_type=video.
Pure-helper test: needs only `config`, runs on any platform.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bin", "lib"))

import wiki_page  # noqa: E402


class TestCategoryFromRawPath(unittest.TestCase):
    def _assert_video(self, raw_path):
        source_type, subdir = wiki_page._source_type_and_subdir_from_raw_path(raw_path)
        self.assertEqual(
            source_type, "video",
            f"{raw_path!r} should map to source_type=video, got {source_type!r}",
        )
        self.assertIn(
            "videos", subdir.replace("\\", "/"),
            f"{raw_path!r} should map to a videos subdir, got {subdir!r}",
        )

    def test_posix_video_path(self):
        self._assert_video("raw/videos/artifacts/video-jNQXAC9IVRw.md")

    def test_windows_backslash_video_path(self):
        # The exact shape native Windows produces — the bug input.
        self._assert_video("raw\\videos\\artifacts\\video-jNQXAC9IVRw.md")

    def test_mixed_separators_video_path(self):
        self._assert_video("raw\\videos/artifacts\\video-jNQXAC9IVRw.md")

    def test_flat_layout_paper_path(self):
        # The docstring claims flat (raw/papers/X.pdf) layouts work too — pin it.
        for rp in ("raw/papers/2401.12345.pdf", "raw\\papers\\2401.12345.pdf"):
            st, subdir = wiki_page._source_type_and_subdir_from_raw_path(rp)
            self.assertEqual(st, "paper", f"{rp!r} should map to paper, got {st!r}")
            self.assertIn("papers", subdir.replace("\\", "/"))

    def test_slug_containing_category_word_not_misclassified(self):
        # A webpage slug literally containing "videos" must stay a webpage —
        # the `/videos/` (slashes both sides) match guards against this.
        st, _ = wiki_page._source_type_and_subdir_from_raw_path(
            "raw/webpages/artifacts/best-videos-of-2024.md")
        self.assertEqual(st, "webpage")

    def test_repo_path_both_separators(self):
        for rp in ("raw/repos/artifacts/owner--repo.md",
                   "raw\\repos\\artifacts\\owner--repo.md"):
            st, _ = wiki_page._source_type_and_subdir_from_raw_path(rp)
            self.assertEqual(st, "repo", f"{rp!r} should map to repo, got {st!r}")

    def test_unknown_path_defaults_to_webpage(self):
        st, subdir = wiki_page._source_type_and_subdir_from_raw_path("nonsense.md")
        self.assertEqual(st, "webpage")
        self.assertIn("webpages", subdir.replace("\\", "/"))

    def test_none_and_empty_safe(self):
        for rp in (None, ""):
            st, _ = wiki_page._source_type_and_subdir_from_raw_path(rp)
            self.assertEqual(st, "webpage")


if __name__ == "__main__":
    unittest.main()
