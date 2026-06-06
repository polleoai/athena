"""Phase 1: vault_root() must resolve correctly in a frozen binary.

Source mode pins the root via __file__ (bin/kb at <root>/bin/kb). A Nuitka/
PyInstaller binary has a useless __file__ (points into the bundle), so it
resolves via the KB_ROOT env contract, then a CWD walk-up to the CLAUDE.md
marker. These tests force the frozen branch with sys.frozen.

Run from vault root:
    python3 -m pytest tests/test_vault_root_resolution.py -v
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

LIB = Path(__file__).resolve().parent.parent / "bin" / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

import kb_dispatch  # type: ignore  # noqa: E402


class VaultRootResolution(unittest.TestCase):
    def setUp(self):
        self._had_frozen = hasattr(sys, "frozen")
        self._frozen_val = getattr(sys, "frozen", None)
        self._had_kb_root = "KB_ROOT" in os.environ
        self._kb_root_val = os.environ.get("KB_ROOT")

    def tearDown(self):
        if self._had_frozen:
            sys.frozen = self._frozen_val
        elif hasattr(sys, "frozen"):
            del sys.frozen
        if self._had_kb_root:
            os.environ["KB_ROOT"] = self._kb_root_val
        else:
            os.environ.pop("KB_ROOT", None)

    def test_source_mode_uses_file(self):
        # Not frozen → repo root (contains bin/kb).
        if hasattr(sys, "frozen"):
            del sys.frozen
        root = Path(kb_dispatch.vault_root())
        self.assertTrue((root / "bin" / "kb").exists())

    def test_frozen_prefers_kb_root_env(self):
        sys.frozen = True
        tmp = Path(tempfile.mkdtemp(prefix="kb-vroot-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        os.environ["KB_ROOT"] = str(tmp)
        self.assertEqual(
            Path(kb_dispatch.vault_root()).resolve(), tmp.resolve()
        )

    def test_frozen_walks_cwd_to_claude_md(self):
        sys.frozen = True
        os.environ.pop("KB_ROOT", None)
        tmp = Path(tempfile.mkdtemp(prefix="kb-vroot-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        (tmp / "CLAUDE.md").write_text("vault marker\n", encoding="utf-8")
        sub = tmp / "a" / "b"
        sub.mkdir(parents=True)
        old = os.getcwd()
        try:
            os.chdir(sub)
            self.assertEqual(
                Path(kb_dispatch.vault_root()).resolve(), tmp.resolve()
            )
        finally:
            os.chdir(old)


if __name__ == "__main__":
    unittest.main()
