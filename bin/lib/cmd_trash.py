"""`kb trash` -- list items currently in the soft-delete trash.

Lifted verbatim from the `trash)` heredoc in bin/kb-legacy. The heredoc's
sys.argv[1] (vault path) becomes the `root` parameter.
"""
from __future__ import annotations

import os
import sys
from typing import List


def handle(argv: List[str], root: str) -> int:
    sys.path.insert(0, os.path.join(root, "bin", "lib"))
    from snapshot import list_trash

    KB = root
    trash = list_trash(KB)
    if not trash:
        print("Trash is empty.")
        return 0

    print(f"Trash ({len(trash)} items):")
    print()
    for name, m in trash:
        desc = m.get("description", m.get("operation", "?"))
        ts = m.get("timestamp", "?")
        count = len(m.get("deleted", m.get("files", []))) + len(m.get("modified", []))
        print(f"  {ts}  {desc}  ({count} files)")
    print()
    print("Use 'kb undo' to restore the most recent item.")
    print("Use 'kb purge' to permanently delete items older than 30 days.")
    return 0
