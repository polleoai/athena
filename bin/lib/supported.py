"""Athena's declared supported-environment matrix + a FUNCTIONAL check.

We deliberately do NOT bundle a Python runtime (a lean Nuitka build is ~50-90 MB
per OS — too heavy for an Obsidian plugin). Instead each release *declares* what
it supports and validates the actual environment by trying to use it.

Why functional, not version-based: a version/metadata check lies. Witnessed
2026-06-17 on a host whose `importlib.metadata` reported pydantic 2.13.4 (matching
the installed pydantic-core 2.46.4) yet whose `import pydantic` still raised a
pydantic-core mismatch — two shadowing installs, metadata finds the good one,
import picks the bad one. The only reliable gate is "does it actually import?".

Policy: support Python >= 3.11 with NO upper cap — the latest Python is supported
as long as a consistent arcus/pydantic installs for it. Never blocklist a version;
detect a broken/missing install and hand the user the one-command repair.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

# --- The declared matrix (bump per release; documented in
#     docs/supported-environments.md) ---------------------------------------
MIN_PYTHON = (3, 11)        # arcus floor. No maximum — validate the install,
                            # not the version number.
MIN_NODE_MAJOR = 18         # deep-capture only (scripts/capture-deep.js).

# The single repair command for a broken/missing dependency set. Pulls a
# self-consistent arcus + pydantic + pydantic-core.
REPAIR_CMD = (
    'pip install --user --force-reinstall "arcus-provider-runtime[html,pdf,office]"'
)


def _check_python() -> tuple[bool, str]:
    v = sys.version_info
    if (v.major, v.minor) < MIN_PYTHON:
        return False, (f"Python {v.major}.{v.minor} is below the minimum "
                       f"{MIN_PYTHON[0]}.{MIN_PYTHON[1]}.")
    return True, f"Python {v.major}.{v.minor}.{v.micro}"


def _check_deps() -> tuple[bool, str]:
    # FUNCTIONAL — actually import; do not trust version/metadata (see module
    # docstring). pydantic is the witnessed failure; arcus is the heavier dep
    # that the same broken pydantic would also take down.
    try:
        import pydantic  # noqa: F401
    except Exception as exc:  # noqa: BLE001
        return False, f"`import pydantic` failed ({type(exc).__name__}: {exc})"
    try:
        import arcus.provider_runtime  # noqa: F401
    except Exception as exc:  # noqa: BLE001
        return False, f"`import arcus` failed ({type(exc).__name__}: {exc})"
    return True, f"pydantic {getattr(pydantic, '__version__', '?')} + arcus import OK"


def _check_node() -> tuple[bool, str]:
    exe = shutil.which("node")
    if not exe:
        return False, "node not found on PATH (needed only for deep-capture)"
    try:
        out = subprocess.run([exe, "--version"], capture_output=True,
                             text=True, timeout=8)
        ver = (out.stdout or "").strip().lstrip("v")
        major = int(ver.split(".")[0])
        if major < MIN_NODE_MAJOR:
            return False, f"node {ver} is below the minimum {MIN_NODE_MAJOR}"
        return True, f"node {ver}"
    except Exception as exc:  # noqa: BLE001
        return False, f"node check failed ({type(exc).__name__}: {exc})"


# --- Auto-remediation: provision an isolated venv Athena controls ----------
# We fix a broken/missing dependency set by building ~/.athena/venv with a
# pinned, self-consistent set — NOT by touching the user's global Python, which
# may have shadowed/conflicting installs a reinstall won't cleanly remove. A
# fresh venv isolates from global site-packages, so even a Python whose GLOBAL
# deps are broken can bootstrap a clean one. The plugin + CLI already prefer
# this venv (see pythonCmd / pythonCmd-equivalent).
VENV_DIR = Path.home() / ".athena" / "venv"
PINNED_DEPS = ["arcus-provider-runtime[html,pdf,office]"]


def venv_python(venv_dir: Path = VENV_DIR) -> Path:
    return venv_dir / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def venv_is_healthy(venv_dir: Path = VENV_DIR) -> bool:
    py = venv_python(venv_dir)
    if not py.exists():
        return False
    try:
        subprocess.run(
            [str(py), "-c", "import pydantic; import arcus.provider_runtime"],
            check=True, capture_output=True, timeout=30)
        return True
    except Exception:  # noqa: BLE001
        return False


def find_bootstrap_python() -> str | None:
    """Any Python >= 3.11 able to create a venv. The current interpreter
    qualifies even if its GLOBAL deps are broken — venv isolates from them."""
    cands = [sys.executable, "python3.13", "python3.12", "python3.11", "python3"]
    if os.name == "nt":
        cands += ["python", "py"]
    for c in cands:
        exe = c if (c and os.path.isabs(c)) else shutil.which(c) if c else None
        if not exe:
            continue
        try:
            r = subprocess.run(
                [exe, "-c", "import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)"],
                capture_output=True, timeout=8)
            if r.returncode == 0:
                return exe
        except Exception:  # noqa: BLE001
            continue
    return None


def repair_into_venv(log=print) -> tuple[bool, str]:
    """Solve a dependency mismatch by (re)building ~/.athena/venv with the
    pinned deps. Returns (ok, message). Idempotent: a healthy venv is a no-op."""
    if venv_is_healthy():
        return True, f"{VENV_DIR} is already healthy — nothing to fix."
    boot = find_bootstrap_python()
    if not boot:
        return False, (f"No Python >= {MIN_PYTHON[0]}.{MIN_PYTHON[1]} found to "
                       "bootstrap a venv. Install Python 3.13, then retry.")
    try:
        log(f"  Creating {VENV_DIR} from {boot} …")
        subprocess.run([boot, "-m", "venv", "--clear", str(VENV_DIR)],
                       check=True, capture_output=True, text=True)
        py = str(venv_python())
        log("  Upgrading pip …")
        subprocess.run([py, "-m", "pip", "install", "--upgrade", "-q", "pip"],
                       check=True, capture_output=True, text=True)
        log("  Installing arcus + deps (may take a minute) …")
        subprocess.run([py, "-m", "pip", "install", "-q", *PINNED_DEPS],
                       check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as exc:
        tail = (exc.stderr or exc.stdout or "").strip().splitlines()[-3:]
        return False, "Provisioning failed:\n      " + "\n      ".join(tail)
    if venv_is_healthy():
        return True, (f"Provisioned {VENV_DIR}. Athena (CLI + plugin) will use it "
                      "automatically — no global Python changes.")
    return False, "Install completed but the deps still don't import."


def check_environment() -> dict:
    """Validate the runtime against the declared matrix.

    Returns {"ok": bool, "checks": [(name, ok, detail), ...], "repair": str}.
    `ok` reflects the HARD requirements (Python + deps); node is soft (only the
    deep-capture path needs it) and is reported but does not fail the gate.
    """
    py_ok, py_detail = _check_python()
    dep_ok, dep_detail = _check_deps()
    node_ok, node_detail = _check_node()
    checks = [
        ("Python >= 3.11 (latest OK)", py_ok, py_detail),
        ("arcus + pydantic import cleanly", dep_ok, dep_detail),
        ("node >= 18 (deep-capture only)", node_ok, node_detail),
    ]
    return {"ok": py_ok and dep_ok, "checks": checks, "repair": REPAIR_CMD}
