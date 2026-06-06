"""Shared runner for verbatim-lifted heredoc bodies.

Several legacy `kb` arms were `python3 - "$KB_ROOT" "$@" << 'EOF' ... EOF`
heredocs: flat top-level scripts that read the vault from `sys.argv[1]`, their
args from `sys.argv[2:]`, and signal exit via `sys.exit(N)`. To port them with
zero behavior change we keep each body BYTE-FOR-BYTE in a sibling `_<cmd>_body.py`
file and exec it here with `sys.argv` reconstructed exactly as the heredoc saw it
(`["-", root, *argv]`, mirroring `python3 - "$KB_ROOT" "$@"`).

We exec at module scope in a fresh namespace -- the same technique as
`cmd_lint.py` -- so a body whose nested helpers mutate module-level names via
`global` behaves identically to its original standalone execution. `__file__` is
deliberately omitted from the namespace: at least one body (reflect) branches on
`'__file__' not in dir()` to locate bin/lib, and the heredoc had no `__file__`.

`sys.exit(N)` raises SystemExit; we translate it into the handler's int return.
SystemExit() with no code, or code None, means success (0), matching the shell.
"""
from __future__ import annotations

import os
import sys
from typing import List


def _is_frozen() -> bool:
    """True inside a Nuitka/PyInstaller binary, where the body .py files are not
    on disk (only the compiled modules exist)."""
    return getattr(sys, "frozen", False) or "__compiled__" in globals()


def run_body(body_filename: str, argv: List[str], root: str) -> int:
    lib_dir = os.path.join(root, "bin", "lib")
    if lib_dir not in sys.path:
        sys.path.insert(0, lib_dir)

    saved_argv = sys.argv
    # Reproduce `python3 - "$KB_ROOT" "$@"`: argv[0] is "-" for stdin scripts,
    # argv[1] the vault, argv[2:] the trailing args.
    sys.argv = ["-", root, *argv]
    try:
        if _is_frozen():
            # Frozen binary: the body .py isn't on disk, so exec-from-source is
            # impossible. Run the COMPILED module's top-level code fresh via the
            # import system. importlib's exec_module re-executes a body's code
            # into a new namespace on every call (verified under Nuitka), so a
            # body invoked repeatedly in one process (e.g. _add_paper_wiki per
            # paper) still runs each time. __name__ is the module name (not
            # "__main__") and __file__ is set; only _reflect_body branches on
            # __file__, and its bin/lib-locating branch is a harmless no-op in
            # the binary (all modules are already compiled in).
            import importlib.util
            modname = (body_filename[:-3] if body_filename.endswith(".py")
                       else body_filename)
            spec = importlib.util.find_spec(modname)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return 0
        # Source mode: exec the body at module scope EXACTLY as before
        # (byte-identical -- ns has __name__ == "__main__" and no __file__).
        body_path = os.path.join(lib_dir, body_filename)
        with open(body_path, "r", encoding="utf-8") as fh:
            source = fh.read()
        ns: dict = {"__name__": "__main__"}
        exec(compile(source, body_path, "exec"), ns)
        return 0
    except SystemExit as e:
        code = e.code
        if code is None:
            return 0
        if isinstance(code, int):
            return code
        # A string/other exit code: print to stderr like CPython does, exit 1.
        sys.stderr.write(f"{code}\n")
        return 1
    finally:
        sys.argv = saved_argv
