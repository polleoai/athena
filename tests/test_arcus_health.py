"""The hourly arcus preflight (scripts/arcus-health.py), PGM 2026-11-22.

Two months of silent arcus failures (no Playwright Chromium on the Max) were
invisible because nothing ever asked "can arcus capture right now?". The
preflight asks every hour, before build:all, and writes the answer where
PGM's backstop reads it.

The rules:
  1. A missing browser gives ok:false with arcus's reason, and exit 1.
  2. A missing yt_dlp gives ok:false with the reason, and exit 1.
  3. A healthy machine gives ok:true, exit 0, and versions.
  4. It fetches only its own loopback page: the guard refuses anything else.
  5. The state goes to <vault>/.athena/state/arcus-health.json (inside the
     vault: Athena never writes outside it), replaced atomically.
  6. sync.sh runs it as its own step, before build, never `|| true`.

Run from vault root (cases 1 and 3 launch arcus's browser):
    python3 -m pytest tests/test_arcus_health.py -v
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from importlib.machinery import SourceFileLoader
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "arcus-health.py"
PY = os.environ.get("ATHENA_PYTHON", sys.executable)

try:
    import arcus.provider_runtime  # type: ignore  # noqa: F401
    HAVE_ARCUS = True
except ImportError:
    HAVE_ARCUS = False


def _load():
    loader = SourceFileLoader("arcus_health_mod", str(SCRIPT))
    spec = importlib.util.spec_from_loader("arcus_health_mod", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


class _Vault(unittest.TestCase):
    def setUp(self):
        self.vault = Path(tempfile.mkdtemp(prefix="athena-health-"))
        self.addCleanup(shutil.rmtree, self.vault, ignore_errors=True)
        self.state = self.vault / ".athena" / "state" / "arcus-health.json"

    def run_script(self, env_extra=None, pre="pass"):
        env = dict(os.environ, **(env_extra or {}))
        code = (f"import sys; {pre}; sys.argv = [{str(SCRIPT)!r}, '--vault', {str(self.vault)!r}]; "
                f"exec(compile(open({str(SCRIPT)!r}).read(), {str(SCRIPT)!r}, 'exec'), "
                "{'__name__': '__main__', '__file__': " + repr(str(SCRIPT)) + "})")
        return subprocess.run([PY, "-c", code], capture_output=True, text=True, env=env,
                              timeout=120, cwd=str(ROOT))

    def result(self):
        return json.loads(self.state.read_text())


@unittest.skipUnless(HAVE_ARCUS, "arcus-provider-runtime not installed")
class Health(_Vault):
    def test_missing_browser_is_not_ok_with_the_reason(self):
        r = self.run_script({"PLAYWRIGHT_BROWSERS_PATH": "/nonexistent-athena-health"})
        self.assertEqual(r.returncode, 1, r.stderr)
        res = self.result()
        self.assertIs(res["ok"], False)
        self.assertIn("Executable doesn't exist", res["reason"])
        self.assertIn("NOT OK", r.stderr)

    def test_missing_ytdlp_is_not_ok(self):
        r = self.run_script(pre="sys.modules['yt_dlp'] = None")
        self.assertEqual(r.returncode, 1, r.stderr)
        res = self.result()
        self.assertIs(res["ok"], False)
        self.assertIn("yt_dlp not importable", res["reason"])

    def test_healthy_is_ok_with_versions(self):
        r = self.run_script()
        self.assertEqual(r.returncode, 0, r.stderr)
        res = self.result()
        self.assertIs(res["ok"], True, res)
        self.assertEqual(res["reason"], "")
        self.assertRegex(res["at"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
        self.assertTrue(res["versions"].get("arcus-provider-runtime"))
        self.assertFalse(list(self.state.parent.glob("*.tmp")), "no half-written file left")


@unittest.skipUnless(HAVE_ARCUS, "arcus-provider-runtime not installed")
class WrongPage(unittest.TestCase):
    def test_a_browser_that_returns_other_text_is_not_ok(self):
        # A launch that "works" but reads a login wall or an empty page is no
        # capture: the check needs its own marker back.
        from unittest import mock
        from arcus.provider_runtime.providers.html import _athena_fetch_page as fp  # type: ignore
        mod = _load()
        with mock.patch.object(fp, "fetch_page", lambda url, deep=False: "Log in to X"):
            reason = mod.check_browser()
        self.assertIn("no page text", reason)


class Policy(unittest.TestCase):
    def test_only_its_own_loopback_page(self):
        pol = _load()._origin_only_policy(8123)
        self.assertIsNone(pol("http://127.0.0.1:8123/"))
        self.assertIsNone(pol("http://127.0.0.1:8123/favicon.ico"))
        for bad in ("http://127.0.0.1:8124/", "https://127.0.0.1:8123/", "http://example.com/",
                    "http://93.184.216.34:80/", "http://localhost:8123/"):
            with self.assertRaises(Exception, msg=bad):
                pol(bad)


class SyncWiring(unittest.TestCase):
    def test_own_step_before_build_never_swallowed(self):
        src = (ROOT / "scripts" / "sync.sh").read_text()
        health = re.search(r"^\s*step arcus-health .*arcus-health\.py.*$", src, re.M)
        build = re.search(r"^\s*step build ", src, re.M)
        self.assertTrue(health, "sync.sh runs the preflight as a step")
        self.assertTrue(build)
        self.assertLess(health.start(), build.start(), "before build:all")
        self.assertNotIn("|| true", health.group(0))


if __name__ == "__main__":
    unittest.main()
