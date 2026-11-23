"""The suite tests the server/ code of the checkout it runs in.

The editable install (a user-site .pth) points at one fixed checkout; run from a
worktree or second clone, `import athena` used to load that checkout's server
code instead (found by agentd's #235 run). tests/conftest.py and
scripts/release-tests.sh put this checkout's server/ first.

Run from vault root:  python3 -m pytest tests/test_server_import_path.py -v
"""
import os
import subprocess
import sys
import unittest
from pathlib import Path

SERVER = (Path(__file__).resolve().parent.parent / "server").resolve()


class ServerImportPath(unittest.TestCase):
    def test_in_process_import_is_this_checkout(self):
        import athena
        self.assertEqual(Path(athena.__file__).resolve().parent.parent, SERVER)

    def test_subprocess_import_is_this_checkout(self):
        out = subprocess.run(
            [sys.executable, "-c", "import athena; print(athena.__file__)"],
            capture_output=True, text=True, env=os.environ, check=True).stdout
        self.assertEqual(Path(out.strip()).resolve().parent.parent, SERVER)


if __name__ == "__main__":
    unittest.main()
