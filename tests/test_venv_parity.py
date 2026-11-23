"""The test venv runs what the user's ~/.athena/venv runs (PGM 2026-11-22).

The Pro test venv resolved pymupdf4llm 2.1 (unpinned, transitive) while the
user's kb runs 1.27.2.3, and the PDF figures tests failed on the Pro only.
Earlier the same day yt-dlp had drifted the same way. Pinning by hand misses
whatever nobody thought to pin, so this checks the whole resolved set
against a snapshot of ~/.athena/venv (tests/fixtures/athena-venv-freeze.txt):

  1. In a venv (the Pro test venv, or ~/.athena/venv itself), every
     installed distribution that is also in the snapshot has the snapshot's
     version, unless it is in ALLOW with a reason.
  2. When the tests run INSIDE ~/.athena/venv (the Max), the snapshot still
     equals that venv: a change to the user's venv must update the snapshot
     and requirements-test.txt in the same commit. (Not merely "a machine
     that has one": the Pro has its own ~/.athena/venv, a different install,
     and the first Pro run failed on exactly that; there check 1 covers the
     test venv.)
  3. Every requirements-test.txt pin of a snapshot package agrees with it.

Refresh after a deliberate venv change:
    ~/.athena/venv/bin/python -m pip freeze --exclude-editable \\
        | grep -v ' @ ' > tests/fixtures/athena-venv-freeze.txt

Run from vault root:
    python3 -m pytest tests/test_venv_parity.py -v
"""
from __future__ import annotations

import importlib.metadata
import os
import re
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SNAPSHOT = ROOT / "tests" / "fixtures" / "athena-venv-freeze.txt"
USER_VENV = Path.home() / ".athena" / "venv"
USER_VENV_PY = USER_VENV / "bin" / "python"


def _running_in_user_venv() -> bool:
    try:
        return USER_VENV.exists() and Path(sys.prefix).resolve() == USER_VENV.resolve()
    except OSError:
        return False

# Packages allowed to differ from the user's venv, each with its reason.
ALLOW: dict[str, str] = {
    "pip": "the venv tool itself, not something kb imports",
    "setuptools": "the venv tool itself",
    "wheel": "the venv tool itself",
}


def norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def parse_freeze(lines) -> dict[str, str]:
    out = {}
    for ln in lines:
        ln = ln.strip()
        if ln and not ln.startswith("#") and "==" in ln and " @ " not in ln:
            name, ver = ln.split("==", 1)
            out[norm(name.split("[")[0])] = ver.strip()
    return out


def snapshot() -> dict[str, str]:
    return parse_freeze(SNAPSHOT.read_text().splitlines())


class VenvParity(unittest.TestCase):
    @unittest.skipUnless(sys.prefix != sys.base_prefix, "only meaningful inside a venv")
    def test_running_venv_matches_the_user_venv(self):
        snap = snapshot()
        installed = {norm(d.metadata["Name"]): d.version for d in importlib.metadata.distributions()
                     if d.metadata["Name"]}
        diff = sorted(f"{k}: here {installed[k]}, user venv {snap[k]}"
                      for k in installed.keys() & snap.keys()
                      if installed[k] != snap[k] and k not in ALLOW)
        self.assertEqual(diff, [], "the test venv drifted from ~/.athena/venv; pin these in "
                         "requirements-test.txt (or ALLOW them with a reason)")

    @unittest.skipUnless(_running_in_user_venv(), "not running inside ~/.athena/venv")
    def test_snapshot_is_the_user_venv(self):
        r = subprocess.run([str(USER_VENV_PY), "-m", "pip", "freeze", "--exclude-editable"],
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        live, snap = parse_freeze(r.stdout.splitlines()), snapshot()
        diff = sorted(f"{k}: venv {live.get(k)}, snapshot {snap.get(k)}"
                      for k in live.keys() | snap.keys()
                      if live.get(k) != snap.get(k) and k not in ALLOW)
        self.assertEqual(diff, [], "~/.athena/venv changed: refresh the snapshot (see the "
                         "docstring) and requirements-test.txt in the same commit")

    def test_pins_agree_with_the_snapshot(self):
        snap = snapshot()
        pins = parse_freeze((ROOT / "requirements-test.txt").read_text().splitlines())
        diff = sorted(f"{k}: pinned {v}, user venv {snap[k]}"
                      for k, v in pins.items() if k in snap and v != snap[k] and k not in ALLOW)
        self.assertEqual(diff, [])


if __name__ == "__main__":
    unittest.main()
