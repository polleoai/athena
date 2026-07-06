"""Lint surfacing: low-quality X / LinkedIn captures must be flagged for
re-capture, by QUALITY SIGNATURE (not path) so good long threads aren't caught.

Three signatures:
  * X /status/ raw with no text — link/image only (the Web-Clipper DOM scrape
    dropped the tweet body; the syndication CDN has the real text).
  * LinkedIn body carrying UI chrome — a `trk=` param, "View profile for" card,
    or feed-actor markup that survived even deep-capture.
  * An unexpanded "…more" / "see more" truncation.

Surface-only (re-capture / re-clean is a network / write op; never auto-fixed).

Run from vault root:
    python3 -m pytest tests/test_lint_social_quality.py -v
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

_MSG = "Low-quality social captures"


def _raw(source: str, body: str, clipped_via: str = "web-clipper") -> str:
    return (
        "---\n"
        'title: "Post"\n'
        f'source: "{source}"\n'
        f'clipped_via: "{clipped_via}"\n'
        "---\n\n# Post\n\n" + body + "\n"
    )


_LONG = "\n\n".join(
    f"Paragraph {i} of a genuine long thread with real substance, well beyond "
    f"the thin-content bar, describing point number {i} in detail."
    for i in range(1, 9)
)


class SocialQualityLint(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kb-social-lint-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        (self.tmp / "bin").symlink_to(ROOT / "bin")
        for d in ("wiki/format/webpages", "raw/webpages/artifacts", "inbox"):
            (self.tmp / d).mkdir(parents=True, exist_ok=True)

    def _write(self, name: str, content: str, *, with_wiki: bool = False):
        (self.tmp / "raw/webpages/artifacts" / name).write_text(
            content, encoding="utf-8")
        if with_wiki:
            # A backing wiki page keeps lint's thin-orphan sweep from trashing a
            # deliberately thin raw before the social-quality check runs (the
            # real thin captures, e.g. aisechub, have a wiki page).
            stem = name[:-3]
            (self.tmp / "wiki/format/webpages" / f"{stem}.md").write_text(
                "---\n"
                f'title: "{stem}"\n'
                f'raw_path: "raw/webpages/artifacts/{name}"\n'
                "source_type: webpage\n"
                "---\n\nWiki summary body.\n",
                encoding="utf-8")

    def _lint(self) -> str:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cmd_lint.handle([], str(self.tmp))
        return buf.getvalue()

    def test_thin_x_image_only_is_flagged(self):
        # No text, no external link — just a twimg image. Genuinely thin (the
        # DOM scrape dropped the tweet text).
        self._write("x-com-a-status-1.md", _raw(
            "https://x.com/a/status/1",
            "<img src=\"https://pbs.twimg.com/media/z.jpg\">"),
            with_wiki=True)
        out = self._lint()
        self.assertIn(_MSG, out)
        self.assertIn("x-com-a-status-1.md", out)

    def test_link_share_tweet_not_flagged(self):
        # The payload IS a substantive external link (a GitHub repo) — the tweet
        # is adequately captured despite little prose, so NOT flagged as thin.
        self._write("x-com-a-status-9.md", _raw(
            "https://x.com/a/status/9",
            "https://github.com/x/y\n\n<img src=\"https://pbs.twimg.com/media/z.jpg\">"),
            with_wiki=True)
        out = self._lint()
        # Not flagged by THIS check (other checks may mention a tiny fixture raw
        # for unrelated reasons) — assert the social-thin signature is absent.
        self.assertNotIn("x-com-a-status-9.md: X post has no text", out)

    def test_good_x_thread_is_not_flagged(self):
        self._write("x-com-a-status-2.md", _raw(
            "https://x.com/a/status/2", _LONG, clipped_via="web-clipper-social"))
        out = self._lint()
        self.assertNotIn("x-com-a-status-2.md", out)

    def test_linkedin_chrome_is_flagged(self):
        self._write("linkedin-com-posts-ugcpost-3.md", _raw(
            "https://www.linkedin.com/posts/someone-ugcpost-3",
            "[View profile for Jane](https://www.linkedin.com/in/jane?trk=public_post_feed-actor-name)\n\n"
            + _LONG, clipped_via="browser-capture"))
        out = self._lint()
        self.assertIn(_MSG, out)
        self.assertIn("linkedin-com-posts-ugcpost-3.md", out)

    def test_clean_linkedin_is_not_flagged(self):
        self._write("linkedin-com-posts-ugcpost-4.md", _raw(
            "https://www.linkedin.com/posts/someone-ugcpost-4", _LONG,
            clipped_via="deep-capture"))
        out = self._lint()
        self.assertNotIn("linkedin-com-posts-ugcpost-4.md", out)

    def test_truncated_is_flagged(self):
        self._write("x-com-a-status-5.md", _raw(
            "https://x.com/a/status/5", _LONG + "\n\nand there was …more"))
        out = self._lint()
        self.assertIn(_MSG, out)
        self.assertIn("x-com-a-status-5.md", out)


if __name__ == "__main__":
    unittest.main()
