"""Unit tests for `kb install` (cmd_install) -- the one command in the
Bash->Python port whose contract intentionally DIFFERS from bin/kb-legacy, so
it is NOT parity-tested. Instead we monkeypatch shutil.which, platform.system,
and the pip subprocess and assert:

  1. the right per-OS guidance prints for each of Windows/Darwin/Linux when a
     system tool is missing,
  2. pip is invoked once per Python dep with `-m pip install --user <dep>`,
  3. exit code reflects pip success/failure.

No real pip install ever runs (subprocess.run is replaced).
"""
from __future__ import annotations

import io
import os
import sys
import unittest
from contextlib import redirect_stdout
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bin", "lib"))
import cmd_install  # noqa: E402


def _fake_proc(returncode=0, stderr=""):
    p = mock.Mock()
    p.returncode = returncode
    p.stdout = ""
    p.stderr = stderr
    return p


class KbInstall(unittest.TestCase):
    def _run(self, *, system, present, pip_rc=0):
        """Run cmd_install.handle with patched env. `present` is the set of
        system tools shutil.which should report as installed."""
        def fake_which(name):
            return "/usr/bin/" + name if name in present else None

        calls = []

        def fake_run(args, **kw):
            calls.append(args)
            return _fake_proc(returncode=pip_rc)

        buf = io.StringIO()
        with mock.patch.object(cmd_install.shutil, "which", side_effect=fake_which), \
             mock.patch.object(cmd_install.platform, "system", return_value=system), \
             mock.patch.object(cmd_install.subprocess, "run", side_effect=fake_run), \
             redirect_stdout(buf):
            rc = cmd_install.handle([], "/vault")
        return rc, buf.getvalue(), calls

    # ── (2) pip invoked with expected args, one per Python dep ──
    def test_pip_invoked_per_dep(self):
        _, _, calls = self._run(system="Linux",
                                 present={"node", "python3", "curl"})
        self.assertEqual(len(calls), len(cmd_install.PY_DEPS))
        for args, dep in zip(calls, cmd_install.PY_DEPS):
            self.assertEqual(args[:4], [sys.executable, "-m", "pip", "install"])
            self.assertIn("--user", args)
            self.assertEqual(args[-1], dep)
        # arcus is the headline dep and must be present.
        self.assertTrue(any("arcus-provider-runtime" in c[-1] for c in calls))
        self.assertTrue(any(c[-1] == "yt-dlp" for c in calls))

    # ── (3) exit code reflects pip success/failure ──
    def test_exit_zero_on_pip_success(self):
        rc, out, _ = self._run(system="Darwin",
                               present={"node", "python3", "curl"}, pip_rc=0)
        self.assertEqual(rc, 0)
        self.assertIn("All set!", out)

    def test_exit_nonzero_on_pip_failure(self):
        rc, out, _ = self._run(system="Linux",
                               present={"node", "python3", "curl"}, pip_rc=1)
        self.assertNotEqual(rc, 0)
        self.assertIn("failed to install", out)

    # ── (1) per-OS guidance when a tool is missing ──
    def test_guidance_windows(self):
        rc, out, _ = self._run(system="Windows", present={"python3", "curl"})
        self.assertEqual(rc, 0)  # pip succeeded; missing system tool != failure
        self.assertIn("node: MISSING", out)
        self.assertIn("winget install OpenJS.NodeJS", out)

    def test_guidance_darwin(self):
        _, out, _ = self._run(system="Darwin", present={"python3", "curl"})
        self.assertIn("node: MISSING", out)
        self.assertIn("brew install node", out)

    def test_guidance_linux(self):
        _, out, _ = self._run(system="Linux", present={"python3", "curl"})
        self.assertIn("node: MISSING", out)
        self.assertIn("apt install nodejs", out)

    def test_no_guidance_when_all_present(self):
        _, out, _ = self._run(system="Darwin",
                              present={"node", "python3", "curl"})
        self.assertNotIn("MISSING", out)
        self.assertIn("All set!", out)

    def test_python_alias_satisfies_python3(self):
        # On Windows python3 is often exposed as `python` -- either presence
        # should mark python3 satisfied.
        _, out, _ = self._run(system="Windows", present={"node", "python", "curl"})
        self.assertIn("python3: OK", out)


if __name__ == "__main__":
    unittest.main()
