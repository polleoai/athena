"""Tests for bin/lib/interpreter_guard.py — the kb self-healing interpreter
selection policy.

Covers the pure decision function `choose_reexec_target`: when kb is launched
under a broken global Python, it must re-exec into the controlled ~/.athena/venv;
when it's already on a trusted/healthy interpreter, it must NOT re-exec (and must
not pay the pydantic-import probe cost on the trusted fast path).

Run from vault root:  python3 -m pytest tests/test_interpreter_guard.py -v
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

LIB = Path(__file__).resolve().parent.parent / "bin" / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

import interpreter_guard as ig  # noqa: E402

VENV = "/home/u/.athena/venv/bin/python"
GLOBAL = "/usr/bin/python3"  # the broken global interpreter
OTHER = "/opt/homebrew/bin/python3.13"


def _raises_if_called():
    def _fn():
        raise AssertionError("current_healthy must not be probed on trusted exe")
    return _fn


class ChooseReexecTarget(unittest.TestCase):
    def test_frozen_binary_never_reexecs(self):
        self.assertIsNone(ig.choose_reexec_target(
            GLOBAL, _raises_if_called(), env={}, venv_py=VENV,
            venv_healthy=lambda: True, frozen=True))

    def test_reexec_flag_prevents_loop(self):
        self.assertIsNone(ig.choose_reexec_target(
            GLOBAL, _raises_if_called(), env={ig.REEXEC_FLAG: "1"},
            venv_py=VENV, venv_healthy=lambda: True, frozen=False))

    def test_already_on_venv_is_trusted_without_probe(self):
        # cur == venv: return None and DO NOT call current_healthy (fast path).
        self.assertIsNone(ig.choose_reexec_target(
            VENV, _raises_if_called(), env={}, venv_py=VENV,
            venv_healthy=lambda: True, frozen=False))

    def test_already_on_athena_python_override_is_trusted(self):
        self.assertIsNone(ig.choose_reexec_target(
            OTHER, _raises_if_called(), env={"ATHENA_PYTHON": OTHER},
            venv_py=VENV, venv_healthy=lambda: True, frozen=False))

    def test_untrusted_but_healthy_interpreter_is_left_alone(self):
        # A non-venv interpreter that CAN import pydantic should keep running.
        self.assertIsNone(ig.choose_reexec_target(
            OTHER, lambda: True, env={}, venv_py=VENV,
            venv_healthy=lambda: True, frozen=False))

    def test_broken_global_reexecs_into_healthy_venv(self):
        self.assertEqual(VENV, ig.choose_reexec_target(
            GLOBAL, lambda: False, env={}, venv_py=VENV,
            venv_healthy=lambda: True, frozen=False))

    def test_broken_global_with_unhealthy_venv_stays_put(self):
        # Nothing healthy to jump to — return None so the actionable pydantic
        # error surfaces ("run kb doctor --fix") instead of a re-exec loop.
        self.assertIsNone(ig.choose_reexec_target(
            GLOBAL, lambda: False, env={}, venv_py=VENV,
            venv_healthy=lambda: False, frozen=False))

    def test_explicit_override_wins_over_venv_without_probe(self):
        # ATHENA_PYTHON set to a non-current interpreter: honor it directly,
        # never probing the venv health.
        def _venv_healthy():
            raise AssertionError("venv_healthy must not be probed when override set")
        self.assertEqual(OTHER, ig.choose_reexec_target(
            GLOBAL, lambda: False, env={"ATHENA_PYTHON": OTHER},
            venv_py=VENV, venv_healthy=_venv_healthy, frozen=False))

    def test_no_self_reexec_when_override_equals_current(self):
        # ATHENA_PYTHON points at the (broken) current interpreter and the venv
        # is unavailable: don't re-exec into ourselves.
        self.assertIsNone(ig.choose_reexec_target(
            GLOBAL, lambda: False, env={"ATHENA_PYTHON": GLOBAL},
            venv_py=None, venv_healthy=lambda: False, frozen=False))


if __name__ == "__main__":
    unittest.main()
