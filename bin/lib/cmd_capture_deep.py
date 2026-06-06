"""`kb capture-deep <url> [--headed] [--reauth]` -- Playwright-driven deep
capture (LinkedIn carousels etc.) via scripts/capture-deep.js.

Ports the `capture-deep)` arm of bin/kb-legacy. With no URL it prints usage to
STDERR and exits 2 (so stdout stays empty -- the parity gate compares stdout).
Otherwise it execs `node scripts/capture-deep.js "$@"` with KB_ROOT exported,
forwarding node's exit code. The node invocation is the network/browser path --
not parity-tested here (covered by host/VM); only the no-arg usage surface is.
"""
from __future__ import annotations

import os
import subprocess
import sys
from typing import List


def handle(argv: List[str], root: str) -> int:
    if not argv:
        sys.stderr.write("Usage: kb capture-deep <url> [--headed] [--reauth]\n")
        sys.stderr.write("\n")
        sys.stderr.write("Auto-paginates LinkedIn carousels and writes a clip-shaped\n")
        sys.stderr.write("markdown file to clippings/. Autoingest then runs the same\n")
        sys.stderr.write("pipeline as a normal Web Clipper drop.\n")
        sys.stderr.write("\n")
        sys.stderr.write("First-time setup: opens browser, log into LinkedIn, close window.\n")
        sys.stderr.write("\n")
        sys.stderr.write("Flags:\n")
        sys.stderr.write("  --headed   force visible browser (debug or re-login)\n")
        sys.stderr.write("  --reauth   delete saved auth + open browser for fresh login\n")
        return 2

    env = dict(os.environ)
    env["KB_ROOT"] = root
    script = os.path.join(root, "scripts", "capture-deep.js")
    return subprocess.run(["node", script, *argv], cwd=root, env=env).returncode
