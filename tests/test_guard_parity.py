"""Athena's two address guards agree: bin/lib/url_safety.py and scripts/lib/url-guard.js.

The browser path (capture-deep, Playwright) checks addresses in JS; every
Python fetch checks them in url_safety. They were written separately, and on
2026-11-23 a security review found the JS one let through IPv4-compatible
::a.b.c.d, ::ffff:0:a.b.c.d and local-use NAT64 64:ff9b:1::/48, all of which
the Python one refused. This runs one table of addresses through both.

Run from vault root:
    python3 -m pytest tests/test_guard_parity.py -v
"""
from __future__ import annotations

import ipaddress
import json
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "bin" / "lib"))

import url_safety  # type: ignore  # noqa: E402

TABLE = [
    # internal
    "127.0.0.1", "10.0.0.1", "169.254.169.254", "100.64.0.1", "192.168.1.1", "0.0.0.0",
    "::1", "::", "fe80::1", "fc00::1", "::ffff:127.0.0.1", "::ffff:10.0.0.1",
    "64:ff9b::7f00:1", "2002:7f00:1::1", "::7f00:1", "::127.0.0.1", "::ffff:0:7f00:1",
    "64:ff9b:1::1",
    # public
    "8.8.8.8", "1.1.1.1", "93.184.216.34", "2606:4700:4700::1111", "::ffff:8.8.8.8",
    "64:ff9b::808:808", "2002:808:808::1",
]


@unittest.skipUnless(shutil.which("node"), "node not installed")
class GuardsAgree(unittest.TestCase):
    def test_same_verdict_for_every_address(self):
        js = subprocess.run(
            ["node", "-e", "const g=require(process.argv[1]);"
             "console.log(JSON.stringify(JSON.parse(process.argv[2]).map(ip=>g.ipIsInternal(ip))))",
             str(ROOT / "scripts" / "lib" / "url-guard.js"), json.dumps(TABLE)],
            capture_output=True, text=True, timeout=30, check=True)
        js_verdicts = json.loads(js.stdout)
        py_verdicts = [url_safety._ip_is_internal(ipaddress.ip_address(ip)) for ip in TABLE]
        diff = [f"{ip}: python={p} js={j}" for ip, p, j in zip(TABLE, py_verdicts, js_verdicts) if p != j]
        self.assertEqual(diff, [])


if __name__ == "__main__":
    unittest.main()
