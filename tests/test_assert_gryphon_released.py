"""scripts/assert-gryphon-released.sh refuses a dev-only Gryphon pin (#193).

A public Athena release must not ship a vendor/gryphon gitlink that
polleoai/gryphon can't resolve. release.yml has the CI guard; this script is
the local mirror. Hermetic: a throwaway parent repo with a gitlink, a local
bare "origin" for the submodule, no network, and no `gh` on PATH.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "assert-gryphon-released.sh"
GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@t", "GIT_CONFIG_GLOBAL": "/dev/null",
           "GIT_CONFIG_NOSYSTEM": "1", "GIT_ALLOW_PROTOCOL": "file"}


def _git(*args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True,
                          env={**os.environ, **GIT_ENV}).stdout.strip()


class AssertGryphonReleased(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        # "polleoai/gryphon": a bare origin whose main holds the released commit.
        work = self.tmp / "gwork"; work.mkdir()
        _git("init", "-q", "-b", "main", cwd=work)
        (work / "f").write_text("released\n")
        _git("add", "f", cwd=work); _git("commit", "-q", "-m", "released", cwd=work)
        self.released = _git("rev-parse", "HEAD", cwd=work)
        origin = self.tmp / "origin.git"
        _git("clone", "-q", "--bare", str(work), str(origin), cwd=self.tmp)
        # Athena: a parent repo with .gitmodules and a populated submodule clone.
        self.parent = self.tmp / "athena"; self.parent.mkdir()
        _git("init", "-q", "-b", "main", cwd=self.parent)
        (self.parent / ".gitmodules").write_text(
            '[submodule "vendor/gryphon"]\n\tpath = vendor/gryphon\n'
            f"\turl = {origin}\n")
        _git("clone", "-q", str(origin), "vendor/gryphon", cwd=self.parent)
        sub = self.parent / "vendor" / "gryphon"
        (sub / "f").write_text("dev only\n")
        _git("commit", "-q", "-am", "dev only", cwd=sub)  # never pushed to origin
        self.dev_only = _git("rev-parse", "HEAD", cwd=sub)
        # PATH without gh: git and the shell's basics only.
        self.bin = self.tmp / "bin"; self.bin.mkdir()
        for tool in ("git", "awk", "grep", "head", "bash", "sed", "cat"):
            found = shutil.which(tool)
            if found:
                (self.bin / tool).symlink_to(found)

    def _pin(self, sha):
        _git("update-index", "--add", "--cacheinfo", f"160000,{sha},vendor/gryphon",
             cwd=self.parent)
        _git("add", ".gitmodules", cwd=self.parent)
        _git("commit", "-q", "-m", "pin", cwd=self.parent)

    def _run(self):
        p = subprocess.run(["bash", str(SCRIPT)], cwd=self.parent, capture_output=True, text=True,
                           env={**os.environ, **GIT_ENV, "PATH": str(self.bin)})
        return p.returncode, p.stdout + p.stderr

    def test_released_pin_passes(self):
        self._pin(self.released)
        rc, out = self._run()
        self.assertEqual(rc, 0, out)
        self.assertIn(f"pinned vendor/gryphon = {self.released}", out)

    def test_dev_only_pin_is_refused(self):
        self._pin(self.dev_only)
        rc, out = self._run()
        self.assertEqual(rc, 1, out)
        self.assertIn("REFUSING", out)

    def test_unpopulated_submodule_without_gh_is_a_stall_not_a_pass(self):
        self._pin(self.released)
        shutil.rmtree(self.parent / "vendor" / "gryphon")
        (self.parent / "vendor" / "gryphon").mkdir()
        rc, out = self._run()
        self.assertEqual(rc, 2, out)
        self.assertIn("CANNOT VERIFY", out)
        # And it read the pin from the parent's gitlink, not Athena's HEAD.
        self.assertIn(f"pinned vendor/gryphon = {self.released}", out)


if __name__ == "__main__":
    unittest.main()
