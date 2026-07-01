"""Lint: raws whose `source:` is an unservable LinkedIn synthetic link are flagged.

Anchor (2026-07-01): LinkedIn posts captured before the unservable-canonical fix
stored the synthetic /posts/{ugcpost,activity,share}-<id> dedup key as their
`source:`. LinkedIn does not serve that form ("Invalid post link"), and the
original resolvable URL was never recorded — so these can't be auto-fixed; the
page must be re-clipped. This lint surfaces them so the dead links are visible.

Run from vault root:
    python3 -m pytest tests/test_lint_linkedin_dead_source.py -v
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


def _raw(source: str) -> str:
    return (
        "---\n"
        'title: "A LinkedIn post"\n'
        f'source: "{source}"\n'
        'clipped_via: "deep-capture"\n'
        "---\n\n# A LinkedIn post\n\n"
        + ("Substantial post body so the orphan-raw check synthesizes rather "
           "than thin-trashes it. " * 8)
        + "\n"
    )


def _wiki(name: str, raw: str, url: str) -> str:
    return (
        "---\n"
        f'title: "{name}"\n'
        'source_type: "webpage"\n'
        f'raw_path: "{raw}"\n'
        f'url: "{url}"\n'
        "tags: [webpage]\n"
        "---\n"
        f"[Source]({url})\n\n## Key Findings\n\n- a finding\n\n## Keywords\n[[webpage]]\n"
    )


class LinkedInDeadSourceLint(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kb-li-dead-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        (self.tmp / "bin").symlink_to(ROOT / "bin")
        for d in ("wiki/format/webpages", "raw/webpages/artifacts", "inbox"):
            (self.tmp / d).mkdir(parents=True, exist_ok=True)

    def _write_raw(self, name: str, source: str):
        raw_rel = f"raw/webpages/artifacts/{name}.md"
        (self.tmp / raw_rel).write_text(_raw(source), encoding="utf-8")
        # A matching wiki page so the raw isn't treated as an orphan (which the
        # lint auto-fix would trash/synthesize before our check runs).
        (self.tmp / "wiki/format/webpages" / f"{name}.md").write_text(
            _wiki(name, raw_rel, source), encoding="utf-8"
        )

    def _lint_output(self) -> str:
        buf = io.StringIO()
        with redirect_stdout(buf):
            cmd_lint.handle([], str(self.tmp))
        return buf.getvalue()

    def test_synthetic_source_is_flagged(self):
        self._write_raw(
            "linkedin-com-posts-ugcpost-7475613415152005121",
            "https://linkedin.com/posts/ugcpost-7475613415152005121",
        )
        out = self._lint_output()
        self.assertIn("LinkedIn source link not resolvable", out)
        self.assertIn("linkedin-com-posts-ugcpost-7475613415152005121", out)

    def test_resolvable_source_not_flagged(self):
        # The post-fix resolvable original must NOT trip the check.
        self._write_raw(
            "linkedin-com-posts-ugcpost-7475613415152005121",
            "https://www.linkedin.com/posts/emilyhartstone_x-share-7475613415152005121-X58e/",
        )
        out = self._lint_output()
        self.assertNotIn("LinkedIn source link not resolvable", out)


if __name__ == "__main__":
    unittest.main()
