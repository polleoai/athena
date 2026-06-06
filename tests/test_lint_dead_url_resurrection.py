"""Lint check #51 — resurrected dead URLs (issue #129 follow-up).

A URL can fail capture (recorded in inbox/url-dead.txt), then succeed on a
later attempt (recorded in inbox/url-resolved.tsv as `captured`) while its dead
record is never cleared. That leaves the same URL recorded as both "dead" and
"captured" — a data inconsistency that can also wrongly suppress a re-attempt.
The lint auto-fix drops the stale dead row(s) whose source_url is now captured.

Must be idempotent and must NOT touch dead rows that are still genuinely dead.

Run from vault root:
    python3 -m pytest tests/test_lint_dead_url_resurrection.py -v
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

_HEADER = "status\tdescription\tsource_url\tresolved_url\ttype\n"
_LIVE_DEAD = "https://example.com/still-dead.pdf"
_RESURRECTED = "https://x.com/u/status/123"


def _row(status: str, url: str, rtype: str = "webpage") -> str:
    return f"{status}\tdesc\t{url}\t{url}\t{rtype}\n"


class DeadUrlResurrectionLint(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kb-resurrect-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        (self.tmp / "bin").symlink_to(ROOT / "bin")
        for d in ("wiki/format/webpages", "raw/webpages/artifacts", "inbox"):
            (self.tmp / d).mkdir(parents=True, exist_ok=True)
        self.dead = self.tmp / "inbox" / "url-dead.txt"
        self.resolved = self.tmp / "inbox" / "url-resolved.tsv"
        # _RESURRECTED is in BOTH (dead + captured); _LIVE_DEAD only in dead.
        self.dead.write_text(
            _HEADER + _row("dead", _RESURRECTED, "tweet") + _row("dead", _LIVE_DEAD, "paper"),
            encoding="utf-8",
        )
        self.resolved.write_text(
            _HEADER + _row("captured", _RESURRECTED, "tweet"),
            encoding="utf-8",
        )

    def _lint(self) -> str:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cmd_lint.handle([], str(self.tmp))
        return buf.getvalue()

    def test_resurrected_row_removed_live_dead_kept(self):
        out = self._lint()
        dead = self.dead.read_text(encoding="utf-8")
        # The captured URL's stale dead row is gone...
        self.assertNotIn(_RESURRECTED, dead)
        # ...but the genuinely-dead URL is untouched, and the header survives.
        self.assertIn(_LIVE_DEAD, dead)
        self.assertTrue(dead.startswith("status\t"))
        self.assertIn("Stale dead-URL records cleared", out)

    def test_idempotent_second_pass_no_change(self):
        self._lint()
        first = self.dead.read_text(encoding="utf-8")
        out2 = self._lint()
        second = self.dead.read_text(encoding="utf-8")
        self.assertEqual(first, second, "second lint pass must not churn url-dead.txt")
        self.assertNotIn("Stale dead-URL records cleared", out2)

    def test_malformed_and_blank_rows_preserved(self):
        """The auto-fix must never eat rows it doesn't understand."""
        self.dead.write_text(
            _HEADER
            + _row("dead", _RESURRECTED, "tweet")
            + "\n"  # blank line
            + "garbage-single-column-row\n"  # malformed (no tabs)
            + _row("dead", _LIVE_DEAD, "paper"),
            encoding="utf-8",
        )
        self._lint()
        dead = self.dead.read_text(encoding="utf-8")
        self.assertNotIn(_RESURRECTED, dead)
        self.assertIn("garbage-single-column-row", dead)
        self.assertIn("\n\n", dead)  # blank line survived
        self.assertIn(_LIVE_DEAD, dead)

    def test_failed_status_row_also_cleared(self):
        """The matcher covers both 'dead' and 'failed' statuses."""
        failed_url = "https://example.com/transient-failed.pdf"
        self.dead.write_text(
            _HEADER + _row("failed", failed_url, "paper") + _row("dead", _LIVE_DEAD, "paper"),
            encoding="utf-8",
        )
        self.resolved.write_text(
            _HEADER + _row("captured", failed_url, "paper"), encoding="utf-8"
        )
        self._lint()
        dead = self.dead.read_text(encoding="utf-8")
        self.assertNotIn(failed_url, dead)
        self.assertIn(_LIVE_DEAD, dead)

    def test_tweet_tracking_suffix_variants_match(self):
        """A tweet that failed with one ?s=/&t= suffix and was captured with a
        different one must still match — canonical-URL normalization collapses
        the tracking params (the dominant X/Twitter resurrection case)."""
        dead_variant = "https://x.com/u/status/999?s=46&t=AAAAAAAAAAAAAAAAAAAAAA"
        capt_variant = "https://x.com/u/status/999?s=12&t=BBBBBBBBBBBBBBBBBBBBBB"
        self.dead.write_text(
            _HEADER + _row("dead", dead_variant, "tweet") + _row("dead", _LIVE_DEAD, "paper"),
            encoding="utf-8",
        )
        self.resolved.write_text(
            _HEADER + _row("captured", capt_variant, "tweet"), encoding="utf-8"
        )
        self._lint()
        dead = self.dead.read_text(encoding="utf-8")
        self.assertNotIn("status/999", dead, "tracking-suffix variant was not matched")
        self.assertIn(_LIVE_DEAD, dead)

    def test_no_resolved_file_is_noop(self):
        self.resolved.unlink()
        out = self._lint()
        dead = self.dead.read_text(encoding="utf-8")
        # Without a resolved ledger nothing can be proven captured → keep all rows.
        self.assertIn(_RESURRECTED, dead)
        self.assertIn(_LIVE_DEAD, dead)
        self.assertNotIn("Stale dead-URL records cleared", out)


if __name__ == "__main__":
    unittest.main()
