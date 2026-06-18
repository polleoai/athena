"""`kb doctor` — check the runtime environment against Athena's supported matrix.

Prints a ✓/✗ report for each declared requirement and, on any failure, the exact
one-command repair. Exits 0 when the hard requirements (Python + deps) pass, 1
otherwise. Validation is functional (it actually imports the deps), so a broken
install that *looks* fine by version is still caught.
"""
from __future__ import annotations

from typing import List

import supported


def handle(argv: List[str], root: str) -> int:
    fix = "--fix" in argv
    result = supported.check_environment()
    print("Athena environment check\n")
    for name, ok, detail in result["checks"]:
        mark = "✓" if ok else "✗"
        print(f"  {mark} {name}\n      {detail}")
    print()

    if result["ok"] and not fix:
        print("All required checks passed. Athena is good to go.")
        return 0

    if not fix:
        print("One or more required checks failed.")
        print(f"  Supported: Python {supported.MIN_PYTHON[0]}.{supported.MIN_PYTHON[1]}+ "
              f"(latest OK) with a consistent arcus + pydantic.")
        print("  Repair automatically:   kb doctor --fix")
        print("  Or do it yourself:")
        print(f"      {result['repair']}")
        print("  Or point Athena at a working interpreter via ATHENA_PYTHON.")
        return 1

    # --fix: build/repair an isolated venv Athena controls (never the global).
    print("Fixing — provisioning an isolated Athena venv (global Python untouched):")
    ok, message = supported.repair_into_venv(log=print)
    print()
    print(("✓ " if ok else "✗ ") + message)
    if not ok:
        print("  Manual fallback:")
        print(f"      {result['repair']}")
    return 0 if ok else 1
