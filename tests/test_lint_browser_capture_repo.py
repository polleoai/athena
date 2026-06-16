"""Lint surfacing: a GitHub repo raw captured via the Obsidian plugin's generic
DOM walker (clipped_via: browser-capture) must be flagged for re-capture.

The DOM walker force-sizes every <img> to width="600", drops the README's
markdown ![]() thumbnails, and keeps invisible spacer/icon images — so a repo
captured that way renders nothing like GitHub (witnessed: roboflow/notebooks,
2026-06-08). The correct path is bin/kb-capture (real README fetch). This lint
check SURFACES the wrong-path raws (it does not auto-fix — re-capture is a
network operation, per discover-and-surface).

Run from vault root:
    python3 -m pytest tests/test_lint_browser_capture_repo.py -v
"""
from __future__ import annotations

import contextlib
import io
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LIB = ROOT / "bin" / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

import cmd_lint  # type: ignore  # noqa: E402


def _repo_raw(clipped_via: str) -> str:
    # Body must be substantial — lint trashes thin orphan raws before the
    # browser-capture check runs, so a stub would be removed and never flagged.
    body = "\n\n".join(
        f"## Section {i}\n\nProse paragraph describing the widgets library "
        f"feature number {i} in enough detail to clear the thin-content bar."
        for i in range(1, 8)
    )
    return (
        "---\n"
        'title: "GitHub - acme/widgets"\n'
        'source: "https://github.com/acme/widgets"\n'
        f'clipped_via: "{clipped_via}"\n'
        "---\n\n# acme/widgets\n\n" + body + "\n"
    )


def _tweet_raw(clipped_via: str, source: str) -> str:
    # Substantial body so the thin-orphan check doesn't trash it first.
    body = "\n\n".join(
        f"Paragraph {i} of the captured post, long enough to clear the "
        f"thin-content bar that runs before the browser-capture check."
        for i in range(1, 8)
    )
    return (
        "---\n"
        'title: "https://t.co/abc123"\n'
        f'source: "{source}"\n'
        f'clipped_via: "{clipped_via}"\n'
        "---\n\n# Some Post\n\n" + body + "\n"
    )


class BrowserCaptureRepoLint(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kb-bcrepo-lint-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        (self.tmp / "bin").symlink_to(ROOT / "bin")
        for d in ("wiki/format/repos", "raw/repos/artifacts",
                  "wiki/format/webpages", "raw/webpages/artifacts", "inbox"):
            (self.tmp / d).mkdir(parents=True, exist_ok=True)

    def _lint_output(self) -> str:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cmd_lint.handle([], str(self.tmp))
        return buf.getvalue()

    def test_browser_capture_repo_is_surfaced(self):
        (self.tmp / "raw/repos/artifacts/github-com-acme-widgets.md").write_text(
            _repo_raw("browser-capture"), encoding="utf-8")
        out = self._lint_output()
        self.assertIn("GitHub repos captured via DOM walker", out)
        self.assertIn("github-com-acme-widgets.md", out)

    def test_kb_capture_repo_is_not_surfaced(self):
        # The correct path leaves clipped_via: kb-capture — must NOT be flagged.
        (self.tmp / "raw/repos/artifacts/github-com-acme-widgets.md").write_text(
            _repo_raw("kb-capture"), encoding="utf-8")
        out = self._lint_output()
        self.assertNotIn("GitHub repos captured via DOM walker", out)

    def test_browser_capture_tweet_is_surfaced(self):
        (self.tmp / "raw/webpages/artifacts/x-com-ren-status-2064900447375085823.md"
         ).write_text(
            _tweet_raw("browser-capture",
                       "https://x.com/Ren/status/2064900447375085823"),
            encoding="utf-8")
        out = self._lint_output()
        self.assertIn("X/Twitter posts captured via DOM walker", out)
        self.assertIn("x-com-ren-status-2064900447375085823.md", out)

    def test_syndication_tweet_is_not_surfaced(self):
        # The correct path leaves clipped_via: tweet-syndication — not flagged.
        (self.tmp / "raw/webpages/artifacts/x-com-ren-status-2064900447375085823.md"
         ).write_text(
            _tweet_raw("tweet-syndication",
                       "https://x.com/Ren/status/2064900447375085823"),
            encoding="utf-8")
        out = self._lint_output()
        self.assertNotIn("X/Twitter posts captured via DOM walker", out)

    def test_browser_capture_generic_webpage_is_not_surfaced(self):
        # A non-tweet webpage legitimately uses the DOM walker — must NOT flag.
        (self.tmp / "raw/webpages/artifacts/example-com-blog-post.md").write_text(
            _tweet_raw("browser-capture", "https://example.com/blog/post"),
            encoding="utf-8")
        out = self._lint_output()
        self.assertNotIn("X/Twitter posts captured via DOM walker", out)


if __name__ == "__main__":
    unittest.main()
