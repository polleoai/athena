"""`kb lint` -- knowledge-base health check + auto-fix.

The lint logic is the VERBATIM body of the `lint)` heredoc in bin/kb-legacy,
preserved byte-for-byte in the sibling file `_lint_body.py`. That body is a flat
top-level script whose nested helpers (e.g. `check`) mutate module-level
counters via `global issues, auto_fixed, total_checks`; `global` only binds
names at module scope, so the body cannot simply be wrapped in a function. We
therefore exec() it in a fresh namespace -- reproducing the heredoc's original
module-scope execution exactly. The heredoc took no trailing args and had no
top-level sys.exit, so lint always returns 0.

Decomposition of the body into sub-modules is explicitly deferred -- a verbatim
lift that reaches byte-for-byte parity with the Bash arm is the deliverable.
"""
from __future__ import annotations

import os
import sys
from typing import List

_BODY_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_lint_body.py")


def handle(argv: List[str], root: str) -> int:
    # bin/lib must be importable: the body does `from wiki_page import ...`,
    # `from collections import Counter`, etc., and adds its own sys.path entry,
    # but seed it here too so the very first import resolves.
    sys.path.insert(0, os.path.join(root, "bin", "lib"))

    if getattr(sys, "frozen", False) or "__compiled__" in globals():
        # Frozen binary: _lint_body.py isn't on disk, so exec-from-source is
        # impossible. Run the COMPILED module fresh, pre-injecting the `root`
        # global the body reads into the module namespace before exec_module
        # (verified under Nuitka). The body's `global issues/auto_fixed/
        # total_checks` bind within this same module namespace, exactly as the
        # source-mode path below. _lint_body references neither __name__ nor
        # __file__, so the namespace difference is inert.
        import importlib.util
        spec = importlib.util.find_spec("_lint_body")
        mod = importlib.util.module_from_spec(spec)
        mod.__dict__["root"] = root
        spec.loader.exec_module(mod)
        return 0

    with open(_BODY_PATH, "r", encoding="utf-8") as fh:
        source = fh.read()
    # Run at module scope: a fresh dict acts as both globals and locals, so the
    # body's top-level `total_checks = 0` and the helpers' `global total_checks`
    # bind the SAME name -- exactly as in the original standalone heredoc.
    ns: dict = {"__name__": "__kb_lint__", "__file__": _BODY_PATH, "root": root}
    exec(compile(source, _BODY_PATH, "exec"), ns)
    return 0
