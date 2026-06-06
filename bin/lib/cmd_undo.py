"""`kb undo` -- restore the most recently trashed item.

Lifted verbatim from the `undo)` heredoc in bin/kb-legacy. The heredoc's
sys.argv[1] (vault path) becomes the `root` parameter; the trailing "$@" args
(it only inspects them for --yes/-y) become `argv`. The heredoc's top-level
sys.exit(N) calls become `return N`.
"""
from __future__ import annotations

import os
import sys
from typing import List


def handle(argv: List[str], root: str) -> int:
    sys.path.insert(0, os.path.join(root, "bin", "lib"))
    from snapshot import restore_last, list_trash

    KB = root
    confirmed = "--yes" in argv or "-y" in argv

    trash = list_trash(KB)
    if not trash:
        print("Nothing to undo. Trash is empty.")
        return 0

    last_name, last_manifest = trash[-1]
    print(f"Last operation: {last_manifest.get('description', last_manifest.get('operation','?'))}")
    print(f"  Time: {last_manifest.get('timestamp','?')}")
    print(f"  Files: {len(last_manifest.get('deleted', last_manifest.get('files',[]))) + len(last_manifest.get('modified',[]))}")
    print()

    if not confirmed:
        try:
            answer = input("Restore? (y/N) ").strip().lower()
            if answer != "y":
                print("Cancelled.")
                return 0
        except (EOFError, KeyboardInterrupt):
            print("\nCancelled.")
            return 0

    ok, msg = restore_last(KB)
    print(msg)
    if ok:
        print("Run 'kb lint' to verify.")
    return 0
