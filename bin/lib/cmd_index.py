"""`kb index` -- build or report the search index.

Lifted verbatim from the `index)` heredoc in bin/kb-legacy. The heredoc passed
"$@" after the vault path; here `argv` holds those same trailing args.
"""
from __future__ import annotations

import os
import sys
from typing import List


def handle(argv: List[str], root: str) -> int:
    sys.path.insert(0, os.path.join(root, "bin", "lib"))
    from search import build_index, index_status

    kb_root = root
    args = argv

    if "--status" in args:
        info = index_status(kb_root)
        print("Search Index Status")
        print("=" * 40)
        for k, v in info.items():
            label = k.replace("_", " ").title()
            print(f"  {label}: {v}")
    else:
        print("Building search index...")
        ok, msg = build_index(kb_root)
        if ok:
            print(f"Done. {msg}")
        else:
            print(f"Error: {msg}")
            return 1
    return 0
