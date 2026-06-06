"""`kb migrate-raws` -- one-time conversion of legacy header-bullet raws to YAML
frontmatter (Phase 4 of the schema refactor). Idempotent; --dry-run previews.

Lifted from the `migrate-raws)` heredoc in bin/kb-legacy.
"""
from __future__ import annotations

import os
import sys
from typing import List


def handle(argv: List[str], root: str) -> int:
    dry_run = "--dry-run" in argv
    sys.path.insert(0, os.path.join(root, "bin", "lib"))
    from migrate_raws import main
    return main(root, dry_run)
