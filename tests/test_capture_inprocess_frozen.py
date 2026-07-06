"""Phase 4 Step 2 — in-process capture under a frozen binary.

In a compiled binary sys.executable is the binary itself and bin/kb-capture is
not on disk, so the capture callers must drive capture IN-PROCESS via
capture.run_capture() instead of spawning a subprocess. Source/dev mode keeps
the subprocess call byte-for-byte (verified by the untouched parity suites).

These tests force the frozen branch with a mock and assert:
  * the in-process path is taken and NO subprocess is spawned,
  * run_capture receives the vault root and args,
  * unified_ingest parses the written-raw-path line out of the captured stdout,
  * run_capture translates a handler die()/sys.exit() into an exit code
    (a SystemExit must never tear down the caller mid-ingest).

Run from vault root:
    python3 -m pytest tests/test_capture_inprocess_frozen.py -v
"""
from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "bin" / "lib"))

import capture  # noqa: E402
import cmd_add  # noqa: E402
import cmd_batch  # noqa: E402
import unified_ingest  # noqa: E402


class RunCaptureSystemExit(unittest.TestCase):
    """run_capture must translate a handler's SystemExit into an exit code."""

    def test_die_exit_1_becomes_1(self):
        with mock.patch.object(capture, "main", side_effect=SystemExit(1)):
            self.assertEqual(capture.run_capture("u"), 1)

    def test_bare_exit_becomes_0(self):
        with mock.patch.object(capture, "main", side_effect=SystemExit(None)):
            self.assertEqual(capture.run_capture("u"), 0)

    def test_exit_0_stays_0(self):
        with mock.patch.object(capture, "main", side_effect=SystemExit(0)):
            self.assertEqual(capture.run_capture("u"), 0)

    def test_plain_return_code_passes_through(self):
        with mock.patch.object(capture, "main", return_value=0) as m:
            self.assertEqual(capture.run_capture("u", "--desc", "d"), 0)
        m.assert_called_once_with(["u", "--desc", "d"])

    def test_vault_root_swapped_and_restored(self):
        original = capture.KB_ROOT
        seen = {}

        def _spy(argv):
            seen["root"] = capture.KB_ROOT
            return 0

        with mock.patch.object(capture, "main", side_effect=_spy):
            capture.run_capture("u", vault_root="/some/other/vault")
        self.assertEqual(seen["root"], "/some/other/vault")
        self.assertEqual(capture.KB_ROOT, original)  # restored


class CmdAddFrozen(unittest.TestCase):
    def test_frozen_uses_inprocess(self):
        with mock.patch.object(cmd_add, "_frozen", return_value=True), \
             mock.patch.object(capture, "run_capture", return_value=0) as rc, \
             mock.patch.object(cmd_add.subprocess, "run") as sub:
            code = cmd_add._capture("/vault", "https://x/a", "--desc", "d")
        self.assertEqual(code, 0)
        rc.assert_called_once_with("https://x/a", "--desc", "d", vault_root="/vault")
        sub.assert_not_called()

    def test_source_mode_uses_subprocess(self):
        with mock.patch.object(cmd_add, "_frozen", return_value=False), \
             mock.patch.object(cmd_add.subprocess, "run",
                               return_value=mock.Mock(returncode=0)) as sub:
            code = cmd_add._capture("/vault", "https://x/a")
        self.assertEqual(code, 0)
        sub.assert_called_once()


class CmdBatchFrozen(unittest.TestCase):
    def test_frozen_uses_inprocess(self):
        with mock.patch.object(cmd_batch, "_frozen", return_value=True), \
             mock.patch.object(capture, "run_capture", return_value=0) as rc, \
             mock.patch.object(cmd_batch.subprocess, "run") as sub:
            ok = cmd_batch._capture("/vault", "https://x/a", "a description")
        self.assertTrue(ok)
        rc.assert_called_once_with(
            "https://x/a", "--desc", "a description", vault_root="/vault")
        sub.assert_not_called()

    def test_frozen_nonzero_is_failure(self):
        with mock.patch.object(cmd_batch, "_frozen", return_value=True), \
             mock.patch.object(capture, "run_capture", return_value=1), \
             mock.patch.object(cmd_batch.subprocess, "run"):
            self.assertFalse(cmd_batch._capture("/vault", "https://x/a", ""))


class UnifiedIngestFrozen(unittest.TestCase):
    def test_frozen_delegate_parses_raw_path_from_captured_stdout(self):
        vault = Path(tempfile.mkdtemp(prefix="kb-frozen-"))
        self.addCleanup(shutil.rmtree, vault, ignore_errors=True)
        inp = unified_ingest.IngestInput(
            vault_root=vault, url="https://arxiv.org/abs/1")

        def _fake(url, vault_root=None):
            # capture normally prints the written path; the caller redirects
            # stdout and parses it.
            print("wrote raw/papers/artifacts/arxiv-1.md")
            return 0

        with mock.patch.object(unified_ingest, "_frozen", return_value=True), \
             mock.patch.object(capture, "run_capture", side_effect=_fake), \
             mock.patch.object(unified_ingest.subprocess, "run") as sub:
            res = unified_ingest._delegate_to_kb_capture(
                inp, {"was_re_routed": False}, "https://arxiv.org/abs/1",
                source_type="paper", extracted_via="test")
        sub.assert_not_called()
        self.assertTrue(str(res.raw_path).endswith("arxiv-1.md"))
        self.assertEqual(res.source_type, "paper")

    def test_frozen_nonzero_raises_ingest_error(self):
        vault = Path(tempfile.mkdtemp(prefix="kb-frozen-"))
        self.addCleanup(shutil.rmtree, vault, ignore_errors=True)
        inp = unified_ingest.IngestInput(
            vault_root=vault, url="https://arxiv.org/abs/1")
        with mock.patch.object(unified_ingest, "_frozen", return_value=True), \
             mock.patch.object(capture, "run_capture", return_value=1), \
             mock.patch.object(unified_ingest.subprocess, "run"):
            with self.assertRaises(unified_ingest.UnifiedIngestError):
                unified_ingest._delegate_to_kb_capture(
                    inp, {"was_re_routed": False}, "https://arxiv.org/abs/1",
                    source_type="paper", extracted_via="test")


if __name__ == "__main__":
    unittest.main()
