"""X self-thread links must not spawn duplicate pages or self-references.

Witnessed 2026-11-22 (JD Pruett / cover-your-ais): a capture of an author's
two follow-up replies carried the links to those replies and to the author's
original post. Lint check 45 treated every one of those same-author status
URLs as a "canonical source" and:
  - queued the reply URLs to inbox/url-new.txt, so auto-ingest captured them
    again as separate pages, re-creating the follow-up page right after
    `kb merge` had folded it into the original post;
  - stamped `discovered_via:` on the original post pointing at the follow-up
    page, which the merge then left dangling.
And `kb merge` headed a section `\\AI incidents\\` for a title holding
`"AI incidents"`: it read the YAML-escaped title without unescaping it.

The rules this file holds:
  1. extract_canonical_urls(text, exclude_author=H) drops X status URLs by
     handle H (the post's own thread); other authors' posts still count.
  2. Lint 45 passes the raw's own author, so a self-thread link is neither
     queued nor cross-linked; another author's post still is.
  3. A lint check removes a discovered_via naming a missing page or the
     page itself, and keeps a valid one.
  4. kb merge unescapes quoted titles in its section headings.

Run from vault root:
    python3 -m pytest tests/test_x_self_thread_links.py -v
"""
from __future__ import annotations

import io
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LIB = ROOT / "bin" / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

import cmd_lint  # type: ignore  # noqa: E402
import cmd_merge  # type: ignore  # noqa: E402
from canonical_source import extract_canonical_urls  # type: ignore  # noqa: E402

AUTHOR = "jdpruettt"
ORIG = f"https://x.com/{AUTHOR}/status/2124285595925217484"
REPLY1 = f"https://x.com/{AUTHOR}/status/2124285598106292408"
REPLY2 = f"https://x.com/{AUTHOR}/status/2124285599687586190"
OTHER = "https://x.com/Saboo_Shubham_/status/2062220865643982875"


class ExtractExcludesOwnThread(unittest.TestCase):
    def test_same_author_status_dropped(self):
        text = f"replies to {ORIG} and {REPLY2}; see also {OTHER}"
        urls = extract_canonical_urls(text, exclude_author=AUTHOR)
        self.assertNotIn(ORIG, urls)
        self.assertNotIn(REPLY2, urls)
        self.assertIn(OTHER, urls, "another author's post is still a canonical source")

    def test_handle_match_is_case_insensitive(self):
        text = "https://twitter.com/JDPruettt/status/2124285595925217484"
        self.assertEqual(extract_canonical_urls(text, exclude_author="jdpruettt"), [])
        self.assertEqual(extract_canonical_urls(ORIG, exclude_author="JDPRUETTT"), [],
                         "the handle passed in may differ in case too")

    def test_default_keeps_everything(self):
        self.assertIn(ORIG, extract_canonical_urls(f"see {ORIG}"),
                      "without exclude_author nothing changes for existing callers")

    def test_other_sources_unaffected(self):
        text = f"{ORIG} https://github.com/jdpru/cover-your-ais"
        self.assertIn("https://github.com/jdpru/cover-your-ais",
                      extract_canonical_urls(text, exclude_author=AUTHOR))


def _page(title, url, raw_rel, extra_fm="", body="body\n"):
    return (f'---\ntitle: "{title}"\nsource_type: "webpage"\n'
            f'raw_path: "{raw_rel}"\nurl: "{url}"\n'
            f"date_added: 2026-11-22\nlast_updated: 2026-11-22\n{extra_fm}"
            f'related:\nsummary: "A test page."\n---\n{body}')


class _Vault(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kb-selfthread-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        (self.tmp / "bin").symlink_to(ROOT / "bin")
        for d in ("wiki/format/webpages", "raw/webpages/artifacts", "inbox"):
            (self.tmp / d).mkdir(parents=True, exist_ok=True)
        (self.tmp / "inbox/url-new.txt").write_text("", encoding="utf-8")

    def raw(self, name, source, body):
        rel = f"raw/webpages/artifacts/{name}"
        (self.tmp / rel).write_text(
            f'---\ntitle: "x"\nsource: "{source}"\n---\n\n# x\n\n{body}\n', encoding="utf-8")
        return rel

    def wiki(self, stem, text):
        p = self.tmp / "wiki/format/webpages" / f"{stem}.md"
        p.write_text(text, encoding="utf-8")
        return p

    def lint(self):
        with redirect_stdout(io.StringIO()):
            cmd_lint.handle([], str(self.tmp))

    def inbox(self):
        return (self.tmp / "inbox/url-new.txt").read_text(encoding="utf-8")


class LintSelfThread(_Vault):
    def test_own_replies_not_queued_but_other_author_is(self):
        rel = self.raw("x-com-jdpruettt-status-2124285598106292408.md", REPLY1,
                       f"Reply to {ORIG}.\n\nReply 2: {REPLY2}\n\nVia {OTHER}")
        self.wiki("X- follow-up", _page("X: follow-up", REPLY1, rel))
        self.lint()
        self.assertNotIn(REPLY2, self.inbox(), "the author's own reply is not a new source")
        self.assertNotIn(ORIG, self.inbox(), "the author's own post is not a new source")
        self.assertIn(OTHER, self.inbox(), "another author's post is still surfaced")

    def test_own_post_page_gets_no_discovered_via_from_a_reply(self):
        orig_rel = self.raw("x-com-jdpruettt-status-2124285595925217484.md", ORIG, "the post")
        orig = self.wiki("X- original", _page("X: original", ORIG, orig_rel))
        rel = self.raw("x-com-jdpruettt-status-2124285598106292408.md", REPLY1,
                       f"Reply to {ORIG}.")
        self.wiki("X- follow-up", _page("X: follow-up", REPLY1, rel))
        self.lint()
        self.assertNotIn("discovered_via", orig.read_text(encoding="utf-8"))


class LintDanglingDiscoveredVia(_Vault):
    def _one(self, stem, via):
        rel = self.raw(f"{stem}.md", f"https://example.com/{stem}", "text " * 200)
        return self.wiki(stem, _page(stem, f"https://example.com/{stem}", rel,
                                     extra_fm=f'discovered_via: "[[{via}]]"\n'))

    def test_missing_target_removed(self):
        p = self._one("page-a", "Page That Was Merged Away")
        self.lint()
        self.assertNotIn("discovered_via", p.read_text(encoding="utf-8"))

    def test_self_target_removed(self):
        p = self._one("page-b", "page-b")
        self.lint()
        self.assertNotIn("discovered_via", p.read_text(encoding="utf-8"))

    def test_plain_name_of_missing_page_removed(self):
        rel = self.raw("page-e.md", "https://example.com/page-e", "text " * 200)
        p = self.wiki("page-e", _page("page-e", "https://example.com/page-e", rel,
                                      extra_fm='discovered_via: "Gone Page"\n'))
        self.lint()
        self.assertNotIn("discovered_via", p.read_text(encoding="utf-8"))

    def test_plain_name_of_existing_page_kept(self):
        self._one("page-g", "page-g")
        rel = self.raw("page-h.md", "https://example.com/page-h", "text " * 200)
        p = self.wiki("page-h", _page("page-h", "https://example.com/page-h", rel,
                                      extra_fm='discovered_via: "page-g"\n'))
        self.lint()
        self.assertIn("discovered_via", p.read_text(encoding="utf-8"),
                      "a plain name of a page that exists is a valid link")

    def test_url_value_kept(self):
        p = self._one("page-f", "page-f")
        p.write_text(p.read_text(encoding="utf-8").replace(
            'discovered_via: "[[page-f]]"', 'discovered_via: "https://x.com/a/status/1"'),
            encoding="utf-8")
        self.lint()
        self.assertIn('discovered_via: "https://x.com/a/status/1"', p.read_text(encoding="utf-8"),
                      "a capture's discovery URL is not a page link")

    def test_valid_target_kept(self):
        self._one("page-c", "page-c")  # the target page exists...
        p = self._one("page-d", "page-c")
        self.lint()
        self.assertIn('discovered_via: "[[page-c]]"', p.read_text(encoding="utf-8"))


class MergeUnescapesTitles(_Vault):
    def test_quoted_title_heading(self):
        t1 = 'X: 80-90% of major \\"AI incidents\\" have no attacker'
        a = self.raw("a.md", "https://example.com/a", "a")
        b = self.raw("b.md", "https://example.com/b", "b")
        self.wiki("A", _page(t1, "https://example.com/a", a))
        self.wiki("B", _page("B", "https://example.com/b", b))
        out = io.StringIO()
        with redirect_stdout(out):
            rc = cmd_merge.handle(["A", "B", "--into", "A"], str(self.tmp))
        self.assertEqual(rc, 0, out.getvalue())
        merged = (self.tmp / "wiki/format/webpages/A.md").read_text(encoding="utf-8")
        self.assertIn('## X: 80-90% of major "AI incidents" have no attacker', merged)
        self.assertNotIn("\\", merged.split("---", 2)[2], "no YAML escapes leak into the body")


if __name__ == "__main__":
    unittest.main()
