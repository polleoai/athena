"""`kb install` -- cross-platform dependency setup.

NEW CONTRACT (intentionally NOT Bash-parity). The legacy `install)` arm shelled
out to `sudo apt/brew/dnf/pacman` to install system packages -- impossible on
native Windows and undesirable (auto-sudo) everywhere. This handler instead:

  1. pip-installs athena's PYTHON deps into the user site (`pip install --user`),
     via the current interpreter -- the one truly cross-platform install path.
  2. DETECTS the required system tools (node, python, curl) and, for any that
     are MISSING, PRINTS per-OS install guidance keyed on platform.system().
     It never auto-installs a system package (no sudo).

Exit 0 on success; non-zero if any pip install fails.
"""
from __future__ import annotations

import platform
import shutil
import subprocess
import sys
from typing import List

# Python deps athena needs at runtime. Mirrors server/pyproject.toml's
# `dependencies` (arcus is the content-extraction kernel) plus yt-dlp, which the
# capture layer shells out to for video sources.
PY_DEPS = [
    "arcus-provider-runtime[html,pdf,office]>=0.7.0",
    "mcp>=1.0",
    "sqlite-vec>=0.1.0",
    "yt-dlp",
]

# System tools athena expects on PATH. (python is the interpreter running this,
# so it's effectively always present, but we still report it for completeness.)
SYSTEM_DEPS = ("node", "python3", "curl")

# Per-OS, per-tool install hints. We print guidance, never run it.
_GUIDANCE = {
    "Windows": {
        "node":    "winget install OpenJS.NodeJS   (or: choco install nodejs)",
        "python3": "winget install Python.Python.3  (or: choco install python)",
        "python":  "winget install Python.Python.3  (or: choco install python)",
        "curl":    "curl ships with Windows 10+; otherwise: winget install curl.curl",
    },
    "Darwin": {
        "node":    "brew install node",
        "python3": "brew install python",
        "python":  "brew install python",
        "curl":    "brew install curl   (macOS ships curl by default)",
    },
    "Linux": {
        "node":    "apt install nodejs  |  dnf install nodejs  |  pacman -S nodejs",
        "python3": "apt install python3  |  dnf install python3  |  pacman -S python",
        "python":  "apt install python3  |  dnf install python3  |  pacman -S python",
        "curl":    "apt install curl  |  dnf install curl  |  pacman -S curl",
    },
}


def _which(name: str) -> bool:
    """python3 may be exposed as `python` on Windows -- treat either as present."""
    if name in ("python3", "python"):
        return bool(shutil.which("python3") or shutil.which("python"))
    return bool(shutil.which(name))


def _os_guidance(system: str, tool: str) -> str:
    table = _GUIDANCE.get(system, _GUIDANCE["Linux"])
    return table.get(tool, f"install {tool} via your system package manager")


def handle(argv: List[str], root: str) -> int:
    system = platform.system()
    print("Knowledge Base — Install Dependencies")
    print("======================================")
    print()
    print(f"Platform: {system}")
    print()

    # 1. Python deps -- the cross-platform install path.
    print("Python dependencies (pip install --user):")
    rc = 0
    # Frozen binary: sys.executable is the kb binary, not a Python interpreter,
    # so `-m pip` would fail; resolve a real python3 on PATH. Source mode keeps
    # sys.executable (the running interpreter) unchanged.
    py = sys.executable
    if getattr(sys, "frozen", False) or "__compiled__" in globals():
        py = shutil.which("python3") or shutil.which("python") or sys.executable
    for dep in PY_DEPS:
        print(f"  {dep} ... ", end="", flush=True)
        proc = subprocess.run(
            [py, "-m", "pip", "install", "--user", dep],
            capture_output=True, text=True,
        )
        if proc.returncode == 0:
            print("ok")
        else:
            print("FAILED")
            err = (proc.stderr or proc.stdout or "").strip()
            if err:
                sys.stderr.write(err + "\n")
            rc = 1
    print()

    # 2. System tools -- detect + print per-OS guidance for any missing.
    print("System tools:")
    missing = []
    for tool in SYSTEM_DEPS:
        present = _which(tool)
        print(f"  {tool}: {'OK' if present else 'MISSING'}")
        if not present:
            missing.append(tool)
    print()

    if missing:
        print(f"Missing system tools on {system} — install them, then re-run 'kb install':")
        for tool in missing:
            print(f"  {tool}: {_os_guidance(system, tool)}")
        print()

    if rc == 0:
        print("Python dependencies installed.")
        if not missing:
            print("All set! Run 'kb help' to get started.")
    else:
        print("Some Python dependencies failed to install. See errors above.")
    return rc
