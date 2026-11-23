"""Long X posts whose full text the syndication CDN no longer serves.

Since 2026-11 cdn.syndication.twimg.com returns `note_tweet: {"id": ...}` with
no `text` for a long post, and `text` is only the ~280-char preview, cut
mid-word. fetch_tweet used that preview as if it were the post, so
`kb add https://x.com/techNmak/status/2122711743369785617` saved a copy that
stopped at "2/ Brendan" with nothing saying it was incomplete (2026-11-17).

Pins: the preview is detected and marked (frontmatter + visible note), the
CLI exits 3 so callers try a fuller route, a fuller capture must really be
the same post, and lint lists marked raws. Offline: no network is touched.
"""
from __future__ import annotations

import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
LIB = ROOT / "bin" / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

import fetch_tweet as ft  # type: ignore  # noqa: E402

URL = "https://x.com/techNmak/status/2122711743369785617"
PREVIEW_TEXT = ("These are literally the best visual resources for learning how Transformers, "
                "LLMs, embeddings, diffusion, and inference work ↓\n\n1/ Transformer "
                "Explainer - Watch GPT process text through embeddings, attention, MLPs and "
                "next-token prediction.\nhttps://t.co/abc\n\n\n2/ Brendan https://t.co/gFDIlf659p")
# The shape the CDN returned on 2026-11-17 (trimmed).
LONG = {
    "id_str": "2122711743369785617",
    "text": PREVIEW_TEXT,
    "display_text_range": [0, len(PREVIEW_TEXT) - len(" https://t.co/gFDIlf659p")],
    "note_tweet": {"id": "Tm90ZVR3ZWV0UmVzdWx0czoyMTIyNzExNzQzMDM4NDA2NjU2"},
    "user": {"name": "Tech with Mak", "screen_name": "techNmak"},
    "entities": {"urls": [{"url": "https://t.co/abc",
                           "expanded_url": "https://poloclub.github.io/transformer-explainer/"}],
                 "media": [{"url": "https://t.co/gFDIlf659p"}]},
}
FULL_TEXT = PREVIEW_TEXT.replace("2/ Brendan https://t.co/gFDIlf659p",
                                 "2/ Brendan Bycroft's LLM Visualization - every layer of a GPT in 3D.\n\n"
                                 "3/ The Illustrated Transformer by Jay Alammar.")


class Detection(unittest.TestCase):
    def test_note_without_text_is_missing(self):
        self.assertTrue(ft.long_text_missing(LONG))

    def test_note_with_text_is_complete(self):
        self.assertFalse(ft.long_text_missing({**LONG, "note_tweet": {"text": FULL_TEXT}}))

    def test_short_tweet_is_complete(self):
        d = dict(LONG); d.pop("note_tweet")
        self.assertFalse(ft.long_text_missing(d))
        self.assertFalse(ft.long_text_missing({**LONG, "note_tweet": None}))

    def test_blank_note_text_is_missing(self):
        self.assertTrue(ft.long_text_missing({**LONG, "note_tweet": {"id": "x", "text": "  "}}))


class MarkedRaw(unittest.TestCase):
    def test_raw_is_marked_in_frontmatter_and_body(self):
        raw = ft.build_raw(URL, LONG)
        fm, body = raw.split("\n---\n", 1)
        self.assertIn("capture_incomplete:", fm)
        self.assertIn("2/ Brendan…", body)
        self.assertIn("Incomplete capture", body)

    def test_complete_long_post_not_marked(self):
        raw = ft.build_raw(URL, {**LONG, "note_tweet": {"text": FULL_TEXT}})
        self.assertNotIn("capture_incomplete", raw)
        self.assertNotIn("Incomplete capture", raw)
        self.assertIn("Jay Alammar", raw)

    def test_frontmatter_stays_valid_yaml_shape(self):
        raw = ft.build_raw(URL, LONG)
        line = next(l for l in raw.splitlines() if l.startswith("capture_incomplete:"))
        self.assertRegex(line, r'^capture_incomplete: "[^"\n]+"$')


class CliExit(unittest.TestCase):
    def _main(self, data):
        buf = io.StringIO()
        with mock.patch.object(ft, "fetch_tweet_json", return_value=data), redirect_stdout(buf):
            rc = ft.main(["fetch_tweet.py", URL])
        return rc, buf.getvalue()

    def test_incomplete_exits_3_with_the_floor_on_stdout(self):
        rc, out = self._main(LONG)
        self.assertEqual(rc, 3)
        self.assertIn("capture_incomplete:", out)

    def test_complete_exits_0(self):
        rc, _ = self._main({**LONG, "note_tweet": {"text": FULL_TEXT}})
        self.assertEqual(rc, 0)


class FullerCapture(unittest.TestCase):
    def test_full_post_is_fuller(self):
        self.assertTrue(ft.fuller_capture("Tech with Mak\n" + FULL_TEXT, LONG))

    def test_x_page_rendering_with_display_urls_is_fuller(self):
        # X's page shows the shortened display URL, not t.co or the full URL.
        page = ("Tech with Mak @techNmak\n" + FULL_TEXT.replace(
            "https://t.co/abc", "poloclub.github.io/transformer-ex\u2026")
            + "\n10:03 AM \u00b7 Nov 16, 2026 \u00b7 12K Views\nLog in Sign up")
        self.assertTrue(ft.fuller_capture(page, LONG))

    def test_x_page_with_display_urls_split_over_lines_is_fuller(self):
        # The real page text for this post (Chrome, 2026-11-17): X splits a
        # display URL over lines and ends it with a lone ellipsis.
        page = ("Tech with Mak\n@techNmak\n" + PREVIEW_TEXT.split("\n1/")[0] +
                "\n\n1/ Transformer Explainer - Watch GPT process text through embeddings, "
                "attention, MLPs and next-token prediction.\n\nhttps://\npoloclub.github.io/"
                "transformer-ex\nplainer/\n\u2026\n\n2/ Brendan Bycroft\u2019s LLM Visualization - "
                "Explore an LLM from architecture down to tensors and operations.\n\nhttps://\n"
                "bbycroft.net/llm\n")
        self.assertTrue(ft.fuller_capture(page, LONG))
        # ...and the same split-link page cut where the preview stops is not.
        self.assertFalse(ft.fuller_capture(page.split("2/ Brendan")[0] + "2/ Brendan\u2026 Show more", LONG))

    def test_preview_then_image_then_quoted_post_is_not(self):
        # gengdaj/status/2061824810267906059's shape: the preview stops, then an
        # image and a quoted post follow. The quoted post is not this post.
        page = (ft.preview_text(LONG) + "\n\n![Image](../../assets/x/1.jpg)\n\n---\n\n"
                "**Quoting Someone @else:**\n\n> A different post entirely, long enough to count.")
        self.assertFalse(ft.fuller_capture(page, LONG))

    def test_login_wall_is_not(self):
        self.assertFalse(ft.fuller_capture("Log in to X. Don't miss what's happening. " * 30, LONG))

    def test_the_preview_itself_is_not(self):
        self.assertFalse(ft.fuller_capture(PREVIEW_TEXT, LONG))

    def test_empty_is_not(self):
        self.assertFalse(ft.fuller_capture("", LONG))

    def test_preview_wrapped_in_page_chrome_is_not(self):
        # Longer than the preview, but it stops where the preview stops: the
        # truncated raw itself, and X's own "Show more" / timestamp rendering.
        self.assertFalse(ft.fuller_capture(ft.build_raw(URL, LONG), LONG))
        for after in ("\u2026 Show more", "... Show more", " 10:03 AM \u00b7 Nov 16, 2026 \u00b7 12K Views",
                      " <img src=x>", " ![Video poster](x)"):
            with self.subTest(after=after):
                page = "Tech with Mak @techNmak\n" + ft.preview_text(LONG) + after + "\nLog in Sign up " * 5
                self.assertFalse(ft.fuller_capture(page, LONG))


class CliCaptureWiring(unittest.TestCase):
    # capture.py's tweet branch is one long network function; pin the wiring.
    SRC = (LIB / "capture.py").read_text(encoding="utf-8")

    def test_incomplete_syndication_is_not_accepted_as_is(self):
        self.assertIn("syn_incomplete = _ft.long_text_missing(_data)", self.SRC)
        self.assertIn("and not syn_incomplete:", self.SRC)

    def test_fallback_requires_a_fuller_capture_and_marks_the_floor(self):
        self.assertIn("_ft.fuller_capture(pw_text, _data)", self.SRC)
        self.assertIn('state["capture_incomplete"] = _ft.INCOMPLETE_REASON', self.SRC)
        self.assertIn('capture_incomplete={state[\'capture_incomplete\']}', self.SRC)


def _run_lint(vault):
    py = os.environ.get("ATHENA_PYTHON", sys.executable)
    return subprocess.run([py, os.path.join(vault, "bin", "kb"), "lint"], cwd=vault,
                          capture_output=True, text=True,
                          env=dict(os.environ, KB_ROOT=vault)).stdout


_PAD = ("\n\nPadding paragraph to clear the thin-orphan threshold so this raw survives "
        "the lint pass and reaches the checks under test. " * 6)


class Lint(unittest.TestCase):
    def _vault(self, **raws):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        Path(d, "CLAUDE.md").write_text("# vault")
        Path(d, "RULES.md").write_text("# RULES\n")
        for sub in ("wiki/format/webpages", "inbox"):
            os.makedirs(os.path.join(d, sub), exist_ok=True)
        art = Path(d, "raw", "webpages", "artifacts"); art.mkdir(parents=True)
        for name, body in raws.items():
            Path(art, f"{name}.md").write_text(body)
        shutil.copytree(ROOT / "bin", Path(d, "bin"),
                        ignore=shutil.ignore_patterns("__pycache__"))
        return d

    def test_marked_raw_is_listed(self):
        out = _run_lint(self._vault(long=ft.build_raw(URL, LONG) + _PAD))
        self.assertIn("Captures marked incomplete", out)
        self.assertIn("long.md", out)

    def test_complete_raw_is_not_listed(self):
        raw = ft.build_raw(URL, {**LONG, "note_tweet": {"text": FULL_TEXT}}) + _PAD
        self.assertNotIn("Captures marked incomplete", _run_lint(self._vault(full=raw)))

    def test_browser_long_post_is_not_called_degraded(self):
        fm = (f'---\ntitle: "T"\nsource: "{URL}"\ncaptured_at: "x"\n'
              'clipped_via: "browser-capture"\n')
        out = _run_lint(self._vault(
            good=fm + 'capture_route: "x-long-post-browser"\n---\n\n# T\n\n' + FULL_TEXT + _PAD))
        self.assertNotIn("captured via DOM walker", out)
        out = _run_lint(self._vault(walker=fm + "---\n\n# T\n\n" + FULL_TEXT + _PAD))
        self.assertIn("captured via DOM walker", out)


if __name__ == "__main__":
    unittest.main()
