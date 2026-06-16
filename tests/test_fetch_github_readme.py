"""Tests for bin/lib/fetch_github_readme.py — the gh-free GitHub README
fetcher the Obsidian plugin uses so repo captures match GitHub instead of
the DOM-walker's mangled output (forced width=600, dropped ![]() thumbnails).

Root cause being guarded (2026-06-08): the plugin's browserCapture path
DOM-walked GitHub repos instead of fetching the README, producing pages
that looked nothing like github.com. This helper fixes the source; these
tests pin its contract. Network is fully mocked — no live calls.

Run from vault root:
    python3 -m unittest tests.test_fetch_github_readme -v
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

_VAULT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_VAULT / "bin" / "lib"))

import fetch_github_readme as fgr  # noqa: E402
import github_readme_postprocess as grp  # noqa: E402

_README = """# roboflow/notebooks

<img src="assets/banner.png" alt="banner" width="600">

![Colab](https://colab.research.google.com/assets/colab-badge.svg)
![local thumb](docs/images/thumb.png)
[![icon](./icons/youtube.png)](https://youtube.com/roboflow)
"""


class BuildRepoRawTest(unittest.TestCase):
    def setUp(self):
        # Mock the two network entry points.
        self._meta = {
            "full_name": "roboflow/notebooks",
            "default_branch": "main",
            "description": "Computer vision tutorials.",
            "stargazers_count": 9450,
            "language": "Jupyter Notebook",
            "topics": ["yolov8", "vlm"],
        }
        fgr.fetch_repo_metadata = lambda owner, repo: self._meta
        fgr.fetch_readme_markdown = lambda owner, repo, branch: _README

    def test_relative_images_rewritten_absolute(self):
        raw = fgr.build_repo_raw("roboflow", "notebooks", "https://github.com/roboflow/notebooks")
        base = "https://raw.githubusercontent.com/roboflow/notebooks/main/"
        # Relative HTML <img> and relative ![]() become absolute.
        self.assertIn(f'src="{base}assets/banner.png"', raw)
        self.assertIn(f"]({base}docs/images/thumb.png)", raw)
        self.assertIn(f"]({base}icons/youtube.png)", raw)  # ./ stripped
        # Already-absolute image left untouched (no double-prefix).
        self.assertIn("](https://colab.research.google.com/assets/colab-badge.svg)", raw)
        self.assertNotIn(f"{base}https://", raw)

    def test_frontmatter_and_provenance(self):
        raw = fgr.build_repo_raw("roboflow", "notebooks", "https://github.com/roboflow/notebooks")
        self.assertTrue(raw.startswith("---\n"))
        self.assertIn('title: "roboflow/notebooks"', raw)
        self.assertIn('source: "https://github.com/roboflow/notebooks"', raw)
        # Distinct provenance — NOT browser-capture (that's the mangled engine
        # the lint check flags for repos).
        self.assertIn('clipped_via: "github-readme"', raw)
        self.assertNotIn("browser-capture", raw)
        self.assertIn('stars: "9450"', raw)
        self.assertIn("**Topics:** yolov8, vlm", raw)

    def test_none_when_metadata_unavailable(self):
        fgr.fetch_repo_metadata = lambda owner, repo: None
        self.assertIsNone(
            fgr.build_repo_raw("x", "y", "https://github.com/x/y"),
            "must return None (→ caller falls back to browser) when metadata fetch fails",
        )

    def test_none_when_readme_unavailable(self):
        fgr.fetch_readme_markdown = lambda owner, repo, branch: None
        self.assertIsNone(fgr.build_repo_raw("x", "y", "https://github.com/x/y"))


class RewriteParityTest(unittest.TestCase):
    """The string-based rewrite the helper uses must be byte-identical to the
    in-place file rewrite the CLI postprocessor uses — one source of truth."""

    def test_text_matches_file_rewrite(self):
        text_out, _ = grp.rewrite_readme_text("o", "r", "main", _README)
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "README.md"
            p.write_text(_README, encoding="utf-8")
            grp.rewrite_readme("o", "r", "main", p)
            self.assertEqual(text_out, p.read_text(encoding="utf-8"))


class TraversalGuard(unittest.TestCase):
    """Regression (2026-06-15 review): a `../` image path must not be rewritten
    to escape this repo's raw.githubusercontent.com prefix."""

    def test_dotdot_image_left_unrewritten(self):
        src = ('<img src="../../evil/x.png"> ![a](../../bad/y.png) '
               '<img src="sub/ok.png"> ![b](img/ok2.png)')
        out, count = grp.rewrite_readme_text("owner", "repo", "main", src)
        # The two traversal paths are preserved verbatim (not pointed off-repo)…
        self.assertIn('"../../evil/x.png"', out)
        self.assertIn('(../../bad/y.png)', out)
        self.assertNotIn("owner/evil", out)
        self.assertNotIn("owner/bad", out)
        # …while the two legitimate relative paths ARE rewritten.
        self.assertIn("raw.githubusercontent.com/owner/repo/main/sub/ok.png", out)
        self.assertIn("raw.githubusercontent.com/owner/repo/main/img/ok2.png", out)
        self.assertEqual(count, 2)

    def test_yaml_escape_strips_newlines(self):
        out = fgr._yaml_escape("repo\ninjected: true")
        self.assertNotIn("\n", out)


if __name__ == "__main__":
    unittest.main()
