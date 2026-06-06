"""C3: lint must SURFACE a pending canonical source, not merely flag it.

The cross-link check in _lint_body.py finds canonical URLs referenced in a
social raw (incl. destination-tweet status URLs via C2). When the destination
isn't ingested yet, the old code printed "(queued, awaiting ingest)" but never
actually wrote to inbox/url-new.txt — so nothing got surfaced. C3 promotes that
flag-only path to an actual queue_canonical_urls() call.

Runs the real lint body against a minimal temp vault whose bin/ is symlinked to
the repo's, so all imports resolve while wiki/raw/inbox are fixtures.

Run from vault root:
    python3 -m pytest tests/test_lint_pending_canonical_surface.py -v
"""
from __future__ import annotations

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

DEST = "https://x.com/Saboo_Shubham_/status/2062220865643982875"


class LintSurfacesPendingCanonical(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kb-lint-c3-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        (self.tmp / "bin").symlink_to(ROOT / "bin")
        for d in ("wiki/format/webpages", "raw/webpages/artifacts", "inbox"):
            (self.tmp / d).mkdir(parents=True, exist_ok=True)
        # Pointer tweet raw: body records the destination tweet (as C1 writes it).
        (self.tmp / "raw/webpages/artifacts/"
                    "x-com-ataiiam-status-2062236697534812299.md").write_text(
            '---\ntitle: "X: ptr"\n'
            'source: "https://x.com/ataiiam/status/2062236697534812299"\n---\n\n'
            f"# ptr\n\n## Links Found\n\n- {DEST}\n", encoding="utf-8")
        # Wiki page referencing that raw; does NOT yet link the destination.
        (self.tmp / "wiki/format/webpages/X- ptr.md").write_text(
            '---\ntitle: "X: ptr"\nsource_type: "webpage"\n'
            'raw_path: "raw/webpages/artifacts/'
            'x-com-ataiiam-status-2062236697534812299.md"\n'
            'url: "https://x.com/ataiiam/status/2062236697534812299"\n'
            "last_updated: 2026-06-04\nrelated:\n---\nbody\n", encoding="utf-8")
        (self.tmp / "inbox/url-new.txt").write_text("", encoding="utf-8")

    def _inbox(self) -> str:
        return (self.tmp / "inbox/url-new.txt").read_text(encoding="utf-8")

    def test_pending_destination_is_written_to_inbox(self):
        cmd_lint.handle([], str(self.tmp))
        self.assertIn(DEST, self._inbox(),
                      "lint should surface the un-ingested destination tweet to inbox")

    def test_idempotent_no_duplicate_queue(self):
        cmd_lint.handle([], str(self.tmp))
        cmd_lint.handle([], str(self.tmp))
        self.assertEqual(self._inbox().count(DEST), 1,
                         "re-running lint must not double-queue the destination")


if __name__ == "__main__":
    unittest.main()
