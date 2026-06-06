"""`kb purge` -- permanently delete trash items older than N days (default 30).

Lifted verbatim from the `purge)` heredoc in bin/kb-legacy. The heredoc's
sys.argv[1] (vault) becomes `root`; sys.argv[2] (optional days) becomes argv[0].
"""
from __future__ import annotations

import os
import sys
from typing import List


def handle(argv: List[str], root: str) -> int:
    sys.path.insert(0, os.path.join(root, "bin", "lib"))
    from snapshot import purge_old, list_trash

    KB = root
    days = 30
    if len(argv) > 0:
        try:
            days = int(argv[0])
        except (ValueError, IndexError):
            print(f"Invalid days value, using default: {days}")

    trash = list_trash(KB)
    if not trash:
        print("Trash is empty. Nothing to purge.")
        return 0

    print(f"Purging trash items older than {days} days...")
    count = purge_old(KB, days)
    if count:
        print(f"Purged {count} items.")
    else:
        print("No items old enough to purge.")

    remaining = list_trash(KB)
    print(f"Remaining in trash: {len(remaining)} items")
    return 0
