"""`kb attach` -- attach a source/page relationship. Verbatim heredoc body in
_attach_body.py; see _heredoc_runner."""
from __future__ import annotations

from typing import List

from _heredoc_runner import run_body


def handle(argv: List[str], root: str) -> int:
    return run_body("_attach_body.py", argv, root)
