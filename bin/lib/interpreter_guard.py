"""Self-healing interpreter selection for the kb CLI entry point.

`kb doctor --fix` provisions a controlled venv at ~/.athena/venv with a
consistent arcus + pydantic. But bin/kb's shebang (`#!/usr/bin/env python3`)
and some callers can still launch kb under a broken GLOBAL Python whose
`import pydantic` fails (witnessed: Homebrew Python 3.14 with a mismatched
pydantic-core). When that happens, re-exec into the controlled venv so ingest
works instead of crashing deep in the pipeline at `import pydantic`.

This closes the gap for every entry into bin/kb that the Obsidian plugin's
`pythonCmd()` already closes for itself:
  - the POSIX shebath path (`./bin/kb ...` under a broken global python3),
  - a plugin that cached a stale/broken interpreter and spawns `python3 bin/kb`,
  - the `capture-deep` child that spawns `bin/kb capture-deep` via the shebang.

Design: `choose_reexec_target` is a pure function (no I/O) so the selection
logic is unit-tested in isolation; `maybe_reexec` wires it to the real
environment and performs the `os.execv`. It runs BEFORE kb_dispatch (and thus
pydantic) is imported, so the entire dispatch runs under the healthy
interpreter. A compiled binary embeds its own interpreter + deps and never
re-execs.
"""
from __future__ import annotations

import os
import sys

# Set on the re-exec'd process so we never loop, and so children (kb-capture)
# inherit the chosen interpreter via ATHENA_PYTHON.
REEXEC_FLAG = "ATHENA_PY_REEXEC"


def _is_frozen() -> bool:
    return getattr(sys, "frozen", False) or "__compiled__" in globals()


def _norm(path):
    return os.path.normcase(os.path.abspath(path)) if path else None


def choose_reexec_target(
    current_exe,
    current_healthy,
    *,
    env,
    venv_py,
    venv_healthy,
    frozen,
):
    """Return the interpreter path to re-exec into, or None to keep the current.

    Pure function (no process/file I/O) so the policy is unit-testable.

      current_exe     -- sys.executable (the interpreter now running kb)
      current_healthy -- callable() -> bool: lazily checks `import pydantic`
                         IN THIS process. Only consulted for an untrusted exe,
                         so a trusted interpreter pays no import cost.
      env             -- os.environ-like mapping
      venv_py         -- path to the ~/.athena/venv interpreter
      venv_healthy    -- callable() -> bool: True iff venv_py imports pydantic
      frozen          -- True inside a compiled binary (never re-exec)
    """
    if frozen:
        return None
    if env.get(REEXEC_FLAG):
        return None  # already re-exec'd once — never loop

    cur = _norm(current_exe)
    override = env.get("ATHENA_PYTHON") or None
    trusted = {_norm(p) for p in (override, venv_py) if p}
    if cur in trusted:
        return None  # already on the selected/venv interpreter; trust it

    if current_healthy():
        return None  # untrusted interpreter, but it works — leave it alone

    # Untrusted AND broken. Prefer an explicit user override, else the venv.
    if override and _norm(override) != cur:
        # The user pointed ATHENA_PYTHON at an interpreter on purpose; honor it
        # without a probe (re-exec'ing there yields an actionable error if it is
        # itself broken, which beats silently crashing the current one).
        return override
    if venv_py and _norm(venv_py) != cur and venv_healthy():
        return venv_py
    return None


def maybe_reexec():
    """Re-exec bin/kb into a healthy interpreter if the current one is broken.

    Call at the very top of bin/kb, after sys.path includes bin/lib but before
    importing kb_dispatch (which transitively imports pydantic). Silent on the
    happy path; never raises.
    """
    if _is_frozen() or os.environ.get(REEXEC_FLAG):
        return
    try:
        import supported  # stdlib-only top-level imports; safe under broken py
    except Exception:
        return  # can't locate the venv contract — fail open (legacy behavior)

    venv_py = str(supported.venv_python())

    def _current_healthy():
        try:
            import pydantic  # noqa: F401
            return True
        except Exception:
            return False

    target = choose_reexec_target(
        sys.executable,
        _current_healthy,
        env=os.environ,
        venv_py=venv_py,
        venv_healthy=supported.venv_is_healthy,
        frozen=False,  # _is_frozen() already short-circuited above
    )
    if not target:
        return

    os.environ[REEXEC_FLAG] = "1"
    os.environ["ATHENA_PYTHON"] = target  # children (kb-capture) inherit it
    script = os.path.abspath(sys.argv[0])
    try:
        os.execv(target, [target, script, *sys.argv[1:]])
    except OSError:
        # Re-exec failed (exotic platform / permissions). Clear the flag so the
        # eventual pydantic ImportError stays actionable, then fall through to
        # run on the current interpreter as before.
        os.environ.pop(REEXEC_FLAG, None)
