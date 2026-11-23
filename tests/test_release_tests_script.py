"""Regression tests for scripts/release-tests.sh (Athena dev issue #241).

The script must never print "Release tests PASSED" when a suite did not really
run. Each case runs the script against a throwaway tree with stub `mktemp`,
`node` and `python3` first on PATH, so the real suite is never run (and never
recursively re-enters this test).

Run from vault root:  python3 -m pytest tests/test_release_tests_script.py -v
"""
from __future__ import annotations

import os
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "release-tests.sh"

PY_SUMMARY_PASS = "3 passed in 0.12s"


def _stub(path: Path, body: str) -> None:
    path.write_text("#!/bin/sh\n" + textwrap.dedent(body))
    path.chmod(0o755)


class ReleaseTestsScript(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.tree = root / "tree"
        (self.tree / "tests").mkdir(parents=True)
        (self.tree / "tests" / "test-a.js").write_text("")
        (self.tree / "tests" / "known-failures.txt").write_text(
            "# known\ntests/test_known.py::test_known\n")
        self.bin = root / "bin"
        self.bin.mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    def run_script(self, *, py_out="", py_rc=0, js_out="# tests 2\n# pass 2\n", js_rc=0,
                   mktemp_fails=False, tmpdir=None, real_node=False, py_collect=""):
        # Stubs read their scripted output from files so any text is safe.
        (self.bin / "py.out").write_text(py_out)
        (self.bin / "py.collect").write_text(py_collect)
        (self.bin / "js.out").write_text(js_out)
        if not real_node:
            _stub(self.bin / "node", f"""\
                cat "{self.bin}/js.out"
                exit {js_rc}
                """)
        _stub(self.bin / "python3", f"""\
            if [ "$3" = "--version" ]; then echo "pytest 8.0.0"; exit 0; fi
            case " $* " in *" --collect-only "*) cat "{self.bin}/py.collect"; exit 0;; esac
            cat "{self.bin}/py.out"
            exit {py_rc}
            """)
        if mktemp_fails:
            _stub(self.bin / "mktemp",
                  'echo "mktemp: Operation not permitted" >&2\nexit 1\n')
        env = dict(os.environ)
        env["PATH"] = f"{self.bin}{os.pathsep}{env.get('PATH', '')}"
        if tmpdir is not None:
            env["TMPDIR"] = tmpdir
        p = subprocess.run(["bash", str(SCRIPT), str(self.tree)], env=env,
                           capture_output=True, text=True, timeout=60)
        return p.returncode, p.stdout + p.stderr

    def assertSuiteFailed(self, rc, out):
        self.assertNotEqual(rc, 0, out)
        self.assertNotIn("Release tests PASSED", out)

    # -- the #241 bug: the log can't be created, so pytest never runs --------

    def test_unwritable_log_is_a_failure(self):
        rc, out = self.run_script(py_out=PY_SUMMARY_PASS + "\n",
                                  mktemp_fails=True,
                                  tmpdir="/nonexistent/athena-241-tmp")
        self.assertSuiteFailed(rc, out)

    def test_nonexistent_tmpdir_is_a_failure(self):
        # Real mktemp: the log must be created under $TMPDIR, so a missing
        # TMPDIR must fail loudly rather than fall back somewhere else.
        rc, out = self.run_script(py_out=PY_SUMMARY_PASS + "\n",
                                  tmpdir="/nonexistent/athena-241-tmp")
        self.assertSuiteFailed(rc, out)

    # -- same class: pytest produced no summary line -------------------------

    def test_missing_pytest_summary_is_a_failure(self):
        rc, out = self.run_script(py_out="", py_rc=1)
        self.assertSuiteFailed(rc, out)

    def test_missing_pytest_summary_with_rc0_is_a_failure(self):
        rc, out = self.run_script(py_out="some noise\n", py_rc=0)
        self.assertSuiteFailed(rc, out)

    def test_failures_reported_but_none_listed_is_a_failure(self):
        # rc 1 and a summary saying something failed, but no FAILED/ERROR
        # lines to compare against known-failures: can't be read as "none new".
        rc, out = self.run_script(py_out="1 failed, 2 passed in 0.12s\n", py_rc=1)
        self.assertSuiteFailed(rc, out)

    # -- same class: the JS step ran no tests --------------------------------

    def test_js_zero_tests_is_a_failure(self):
        rc, out = self.run_script(py_out=PY_SUMMARY_PASS + "\n",
                                  js_out="# tests 0\n# pass 0\n")
        self.assertSuiteFailed(rc, out)

    # -- same class, real node: Node's runner counts a test file that defines
    # nothing as one passing test ("# tests 1 / # pass 1"), and an
    # all-skipped file exits 0 with "pass 0". A "tests >= 1" guard passes
    # both (voiceagent 2026-11-09 and 2026-11-17, via PGM). Real node here,
    # since the point is what node actually prints.

    def _js_files(self, **files):
        (self.tree / "tests" / "test-a.js").unlink()
        for name, body in files.items():
            (self.tree / "tests" / f"{name}.js").write_text(body)

    REAL = 'const { test } = require("node:test");\ntest("real", () => {});\n'

    def test_real_node_emptied_file_is_a_failure(self):
        self._js_files(**{"test-real": self.REAL, "test-empty": '"use strict";\n'})
        rc, out = self.run_script(py_out=PY_SUMMARY_PASS + "\n", real_node=True)
        self.assertSuiteFailed(rc, out)
        self.assertIn("test-empty.js", out)

    def test_real_node_all_skipped_file_is_a_failure(self):
        self._js_files(**{"test-real": self.REAL, "test-skipped":
            'const { test } = require("node:test");\n'
            'test("a", { skip: true }, () => {});\ntest("b", { todo: true }, () => {});\n'})
        rc, out = self.run_script(py_out=PY_SUMMARY_PASS + "\n", real_node=True)
        self.assertSuiteFailed(rc, out)

    def test_real_node_only_emptied_files_is_a_failure(self):
        self._js_files(**{"test-empty": "", "test-empty2": "// nothing\n"})
        rc, out = self.run_script(py_out=PY_SUMMARY_PASS + "\n", real_node=True)
        self.assertSuiteFailed(rc, out)

    def test_real_node_real_tests_pass(self):
        self._js_files(**{"test-real": self.REAL, "test-real2": self.REAL})
        rc, out = self.run_script(py_out=PY_SUMMARY_PASS + "\n", real_node=True)
        self.assertEqual(rc, 0, out)
        self.assertIn("Release tests PASSED", out)

    def test_js_skipped_summary_is_a_failure(self):
        rc, out = self.run_script(py_out=PY_SUMMARY_PASS + "\n",
                                  js_out="# tests 3\n# pass 2\n# skipped 1\n")
        self.assertSuiteFailed(rc, out)

    def test_py_all_skipped_is_a_failure(self):
        rc, out = self.run_script(py_out="5 skipped in 0.10s\n")
        self.assertSuiteFailed(rc, out)
        self.assertIn("passed no tests", out)

    def test_py_emptied_file_is_a_failure(self):
        for f in ("test_x.py", "test_y.py"):
            (self.tree / "tests" / f).write_text("")
        rc, out = self.run_script(py_out=PY_SUMMARY_PASS + "\n",
                                  py_collect="tests/test_x.py::test_one\n")
        self.assertSuiteFailed(rc, out)
        self.assertIn("tests/test_y.py", out)

    def test_py_every_file_collected_passes(self):
        for f in ("test_x.py", "test_y.py"):
            (self.tree / "tests" / f).write_text("")
        rc, out = self.run_script(py_out=PY_SUMMARY_PASS + "\n",
                                  py_collect="tests/test_x.py::test_one\n"
                                             "tests/test_y.py::C::test_two[1]\n")
        self.assertEqual(rc, 0, out)
        self.assertIn("Release tests PASSED", out)

    def test_js_no_summary_is_a_failure(self):
        rc, out = self.run_script(py_out=PY_SUMMARY_PASS + "\n", js_out="")
        self.assertSuiteFailed(rc, out)

    # -- the passes that must still pass -------------------------------------

    def test_clean_run_passes(self):
        rc, out = self.run_script(py_out=PY_SUMMARY_PASS + "\n")
        self.assertEqual(rc, 0, out)
        self.assertIn("Release tests PASSED", out)

    def test_spec_reporter_summary_passes(self):
        rc, out = self.run_script(py_out=PY_SUMMARY_PASS + "\n",
                                  js_out="ℹ tests 5\nℹ pass 5\n")
        self.assertEqual(rc, 0, out)
        self.assertIn("Release tests PASSED", out)

    def test_real_world_summary_with_subtests_passes(self):
        # The exact summary line athena's own suite printed on 2026-11-08.
        rc, out = self.run_script(
            py_out="FAILED tests/test_known.py::test_known - boom\n"
                   "1 failed, 619 passed, 5 warnings, 50 subtests passed "
                   "in 476.74s (0:07:56)\n",
            py_rc=1)
        self.assertEqual(rc, 0, out)
        self.assertIn("Release tests PASSED", out)

    def test_only_known_failures_passes(self):
        rc, out = self.run_script(
            py_out="FAILED tests/test_known.py::test_known - boom\n"
                   "1 failed, 2 passed in 0.12s\n",
            py_rc=1)
        self.assertEqual(rc, 0, out)
        self.assertIn("Release tests PASSED", out)

    def test_new_failure_fails(self):
        rc, out = self.run_script(
            py_out="FAILED tests/test_new.py::test_new - boom\n"
                   "1 failed, 2 passed in 0.12s\n",
            py_rc=1)
        self.assertSuiteFailed(rc, out)
        self.assertIn("tests/test_new.py::test_new", out)


if __name__ == "__main__":
    unittest.main()
