"""`kb schema-check` -- verify (or fix) raw/wiki schema invariants.

Lifted from the `schema-check)` heredoc in bin/kb-legacy. The Bash arm parsed
two flags (--fix-urls / --fix-merged-singletons) into a MODE, then called into
kb_schema_check. We translate the same flag parsing and call the same module.
"""
from __future__ import annotations

import os
import sys
from typing import List


def handle(argv: List[str], root: str) -> int:
    mode = "check"
    for arg in argv:
        if arg == "--fix-urls":
            mode = "fix-urls"
        elif arg == "--fix-merged-singletons":
            mode = "fix-merged-singletons"

    sys.path.insert(0, os.path.join(root, "bin", "lib"))
    from kb_schema_check import main, fix_urls, fix_merged_singletons

    if mode == "fix-urls":
        return fix_urls(root)
    if mode == "fix-merged-singletons":
        return fix_merged_singletons(root)
    return main(root)
