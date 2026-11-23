"""The launchd plists ship as templates, not with one user's home paths.

scripts/com.athena.{sync,autoingest}.plist carried /Users/<maintainer>/... and
went public that way (found by the public-export scan, 2026-11-23). They now
hold __ATHENA_VAULT__ and __HOME__, and scripts/install-sync-launchd.sh fills
them in from the vault the script lives in and $HOME when it installs.

Run from vault root:
    python3 -m pytest tests/test_launchd_templates.py -v
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
PLISTS = ("com.athena.sync.plist", "com.athena.autoingest.plist")


class Templates(unittest.TestCase):
    def test_no_home_path_in_the_shipped_plists(self):
        for name in PLISTS:
            text = (SCRIPTS / name).read_text()
            self.assertIsNone(re.search(r"/Users/[^/<\s]+", text), name)
            self.assertIn("__ATHENA_VAULT__", text, name)

    def test_install_renders_them_for_this_user(self):
        home = Path(tempfile.mkdtemp(prefix="athena-launchd-"))
        self.addCleanup(shutil.rmtree, home, ignore_errors=True)
        r = subprocess.run(["bash", str(SCRIPTS / "install-sync-launchd.sh"), "--render-only", str(home / "out")],
                           env=dict(os.environ, HOME=str(home)), capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        vault = str(ROOT.resolve())
        for name in PLISTS:
            text = (home / "out" / name).read_text()
            self.assertNotIn("__ATHENA_VAULT__", text, name)
            self.assertNotIn("__HOME__", text, name)
            self.assertIn(vault, text, name)
        self.assertIn(f"<string>{home}</string>", (home / "out" / "com.athena.sync.plist").read_text())


if __name__ == "__main__":
    unittest.main()
