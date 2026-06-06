"""`kb request-feature "desc"` (alias `kb feature`) -- guidance for filing a
feature request upstream.

The legacy `request-feature|feature)` arm is a pure sequence of echo lines (the
actual issue filing happens in the AI session); reproduced verbatim here.
"""
from __future__ import annotations

from typing import List


def handle(argv: List[str], root: str) -> int:
    print("Request a Feature")
    print("=================")
    print("")
    print("In your AI session, just say: kb request-feature")
    print("")
    print("The AI will:")
    print("  1. Ask what you'd like Athena to do")
    print("  2. Explore your use case with follow-up questions")
    print("  3. Clarify edge cases and expected behavior")
    print("  4. Draft a detailed feature request")
    print("  5. Show you the draft for approval")
    print("  6. File it as a GitHub issue")
    return 0
