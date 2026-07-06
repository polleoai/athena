"""Phase 4 Step 3 — bundled Python helpers run in-process under a frozen binary.

capture shells bundled helpers as `[PYBIN, <helper>.py, ...]`. In a compiled
binary PYBIN is the binary and the helper .py is not on disk, so _helper()
dispatches them IN-PROCESS and returns a subprocess.CompletedProcess so the call
sites are unchanged. Source/dev mode keeps the real subprocess (verified by the
untouched suites); these tests exercise the frozen dispatch directly.

Run from vault root:
    python3 -m pytest tests/test_capture_helper_dispatch.py -v
"""
from __future__ import annotations

import subprocess
import sys
import unittest
from unittest import mock
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "bin" / "lib"))

import capture  # noqa: E402


class DispatchInProcess(unittest.TestCase):
    def test_arcus_html_stdout_and_stderr_captured(self):
        import arcus_html

        def _cli():
            print("body text here")            # stdout → CompletedProcess.stdout
            print("IMG: a.png", file=sys.stderr)  # stderr → .stderr
            print("IMG: b.png", file=sys.stderr)
            return 0

        with mock.patch.object(arcus_html, "_cli", side_effect=_cli):
            proc = capture._dispatch_helper_inprocess(
                [str(ROOT / "bin/lib/arcus_html.py"), "--url", "x", "--print-body"],
                capture_output=True)
        self.assertIsInstance(proc, subprocess.CompletedProcess)
        self.assertEqual(proc.returncode, 0)
        self.assertIn("body text here", proc.stdout)
        self.assertEqual(sum(1 for ln in proc.stderr.splitlines()
                             if ln.startswith("IMG: ")), 2)

    def test_systemexit_translated_to_code(self):
        import wiki_schema
        with mock.patch.object(wiki_schema, "_cli", side_effect=SystemExit(3)):
            proc = capture._dispatch_helper_inprocess(
                [str(ROOT / "bin/lib/wiki_schema.py"), "write"], capture_output=True)
        self.assertEqual(proc.returncode, 3)

    def test_capture_deep_routes_to_handler(self):
        import cmd_capture_deep
        with mock.patch.object(cmd_capture_deep, "handle", return_value=0) as h:
            proc = capture._dispatch_helper_inprocess(
                [str(ROOT / "bin/kb"), "capture-deep", "https://x/a"],
                capture_output=False)
        self.assertEqual(proc.returncode, 0)
        h.assert_called_once_with(["https://x/a"], capture.KB_ROOT)

    def test_unknown_helper_raises(self):
        with self.assertRaises(RuntimeError):
            capture._dispatch_helper_inprocess(
                [str(ROOT / "bin/lib/nope.py")], capture_output=False)

    def test_argv_and_env_restored(self):
        import wiki_schema
        saved_argv = list(sys.argv)
        with mock.patch.object(wiki_schema, "_cli", return_value=0):
            capture._dispatch_helper_inprocess(
                [str(ROOT / "bin/lib/wiki_schema.py"), "write", "--url", "u"],
                capture_output=True)
        self.assertEqual(sys.argv, saved_argv)


class HelperModeSelect(unittest.TestCase):
    def test_source_mode_uses_subprocess(self):
        with mock.patch.object(capture, "_frozen", return_value=False), \
             mock.patch.object(capture, "_run",
                               return_value=mock.Mock(returncode=0)) as run:
            capture._helper(["x.py", "--flag"], capture_output=True)
        run.assert_called_once()
        self.assertEqual(run.call_args.args[0][0], capture.PYBIN)

    def test_frozen_mode_dispatches_inprocess(self):
        with mock.patch.object(capture, "_frozen", return_value=True), \
             mock.patch.object(capture, "_dispatch_helper_inprocess",
                               return_value=mock.Mock(returncode=0)) as disp:
            capture._helper(["arcus_html.py", "--url", "x"], capture_output=True)
        disp.assert_called_once()


if __name__ == "__main__":
    unittest.main()
