"""`kb report-bug "desc"` -- guidance for filing a bug upstream.

The legacy `report-bug)` arm is a pure sequence of echo lines (the actual issue
filing happens in the AI session); reproduced verbatim here.
"""
from __future__ import annotations

from typing import List


def handle(argv: List[str], root: str) -> int:
    print("Report a Bug")
    print("============")
    print("")
    print("In your AI session, just say: kb report-bug")
    print("")
    print("The AI will:")
    print("  1. Ask you what went wrong")
    print("  2. Reproduce the issue and verify it's a bug")
    print("  3. Ask clarifying questions if needed")
    print("  4. Draft a detailed bug report")
    print("  5. Show you the draft for approval")
    print("  6. File it as a GitHub issue")
    return 0
