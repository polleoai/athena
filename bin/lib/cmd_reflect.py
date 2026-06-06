"""`kb reflect` -- gather journal/session data for pattern analysis.

Verbatim heredoc body in _reflect_body.py; see _heredoc_runner. The full report
invokes bin/lib/reflect on real vault data; the deterministic surface (--help)
is parity-tested, full behavior is covered by host/VM runs.
"""
from __future__ import annotations

from typing import List

from _heredoc_runner import run_body


def handle(argv: List[str], root: str) -> int:
    return run_body("_reflect_body.py", argv, root)
