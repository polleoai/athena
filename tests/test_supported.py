"""Tests for bin/lib/supported.py — the declared support matrix + functional check.

Run from vault root:  python3 -m pytest tests/test_supported.py -v
"""
from __future__ import annotations

import sys
import unittest
import unittest.mock as mock
from pathlib import Path

LIB = Path(__file__).resolve().parent.parent / "bin" / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

import supported  # noqa: E402


class SupportMatrix(unittest.TestCase):
    def test_no_upper_python_cap(self):
        # Policy: latest Python is supported — only a minimum is declared.
        self.assertEqual(supported.MIN_PYTHON, (3, 11))
        self.assertFalse(hasattr(supported, "MAX_PYTHON"))

    def test_repair_command_pulls_consistent_deps(self):
        self.assertIn("arcus-provider-runtime", supported.REPAIR_CMD)
        self.assertIn("force-reinstall", supported.REPAIR_CMD)

    def test_check_environment_shape(self):
        result = supported.check_environment()
        self.assertIn("ok", result)
        self.assertIn("repair", result)
        names = [c[0] for c in result["checks"]]
        self.assertTrue(any("Python" in n for n in names))
        self.assertTrue(any("pydantic" in n for n in names))

    def test_python_below_minimum_is_rejected_by_version(self):
        Fake = type("V", (), {"major": 3, "minor": 9, "micro": 0})
        with mock.patch.object(supported.sys, "version_info", Fake):
            ok, detail = supported._check_python()
        self.assertFalse(ok)
        self.assertIn("below the minimum", detail)

    def test_latest_python_is_not_rejected_for_being_new(self):
        # A future Python (e.g. 3.99) must pass the VERSION gate — the deps
        # check, not the version number, decides whether it actually works.
        Fake = type("V", (), {"major": 3, "minor": 99, "micro": 0})
        with mock.patch.object(supported.sys, "version_info", Fake):
            ok, _ = supported._check_python()
        self.assertTrue(ok)

    def test_broken_dep_import_is_caught_functionally(self):
        # A version check would miss a shadowed/corrupt install; the functional
        # import does not. Simulate `import pydantic` raising.
        real_import = __builtins__["__import__"] if isinstance(__builtins__, dict) \
            else __builtins__.__import__

        def boom(name, *a, **k):
            if name == "pydantic":
                raise SystemError("pydantic-core mismatch")
            return real_import(name, *a, **k)

        with mock.patch("builtins.__import__", side_effect=boom):
            ok, detail = supported._check_deps()
        self.assertFalse(ok)
        self.assertIn("pydantic", detail)


class RepairIntoVenv(unittest.TestCase):
    def test_healthy_venv_is_a_noop(self):
        with mock.patch.object(supported, "venv_is_healthy", return_value=True):
            ok, msg = supported.repair_into_venv(log=lambda *_: None)
        self.assertTrue(ok)
        self.assertIn("already healthy", msg)

    def test_no_bootstrap_python_guides_to_install(self):
        with mock.patch.object(supported, "venv_is_healthy", return_value=False), \
                mock.patch.object(supported, "find_bootstrap_python", return_value=None):
            ok, msg = supported.repair_into_venv(log=lambda *_: None)
        self.assertFalse(ok)
        self.assertIn("Install Python", msg)

    def test_provisions_venv_when_broken(self):
        # venv broken at start, healthy after install; bootstrap python found.
        with mock.patch.object(supported, "venv_is_healthy", side_effect=[False, True]), \
                mock.patch.object(supported, "find_bootstrap_python", return_value="/usr/bin/python3"), \
                mock.patch.object(supported.subprocess, "run") as run:
            ok, msg = supported.repair_into_venv(log=lambda *_: None)
        self.assertTrue(ok)
        self.assertIn("Provisioned", msg)
        # venv creation + pip upgrade + dep install = 3 subprocess calls
        self.assertEqual(run.call_count, 3)
        # never the user's global — always into VENV_DIR
        venv_arg = str(supported.VENV_DIR)
        self.assertTrue(any(venv_arg in " ".join(map(str, c.args[0])) for c in run.call_args_list))

    def test_repair_targets_an_isolated_venv_not_global(self):
        # The pinned deps go into ~/.athena/venv, not a global site.
        self.assertTrue(str(supported.VENV_DIR).endswith(".athena/venv")
                        or "athena" in str(supported.VENV_DIR))
        self.assertIn("arcus-provider-runtime", supported.PINNED_DEPS[0])


if __name__ == "__main__":
    unittest.main()
