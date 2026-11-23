"""`kb audit` -- read the audit trail of destructive kb operations (#180).

    kb audit tail [N]       the last N entries (default 20), oldest first
    kb audit grep <text>    entries containing <text> (case-insensitive, any
                            field), across the rotated files too; exit 1 when
                            nothing matches, like grep
    kb audit record <tool> <summary> <allow|deny> <reason>
                            internal: how the plugin logs a chat confirmation
                            the user declined (bin/lib/audit.py writes it)

Read-only apart from `record`. Entries come from .athena/audit.jsonl and its
rotations audit.5.jsonl (oldest) .. audit.1.jsonl, in time order.
"""
from __future__ import annotations

import json
import os
import sys
import time
from typing import List

USAGE = ("Usage: kb audit tail [N]\n"
         "       kb audit grep <text>")


def _files(root: str) -> List[str]:
    import audit
    cur = audit.audit_path(root)
    base = cur[: -len(".jsonl")]
    rotated = [f"{base}.{n}.jsonl" for n in range(audit.KEEP, 0, -1)]
    return [p for p in rotated + [cur] if os.path.isfile(p)]


def _raw_lines(root: str) -> List[str]:
    out: List[str] = []
    for p in _files(root):
        with open(p, "r", encoding="utf-8", errors="replace") as fh:
            out += [l.rstrip("\n") for l in fh if l.strip()]
    return out


def _fmt(line: str) -> str:
    try:
        e = json.loads(line)
        ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(int(e["ts"]) / 1000))
    except (ValueError, KeyError, TypeError):
        return f"(unreadable entry) {line[:200]}"
    why = e.get("reason") or "-"
    extra = f", {e['files']} file(s)" if isinstance(e.get("files"), int) else ""
    return (f"{ts}  {str(e.get('decision', '?')):5}  {str(e.get('tool', '?')):12}  "
            f"{e.get('args_summary', '')}  — {why} ({e.get('source', '?')}{extra})")


def handle(argv: List[str], root: str) -> int:
    sys.path.insert(0, os.path.join(root, "bin", "lib"))
    if not argv:
        print(USAGE)
        return 1
    sub, rest = argv[0], argv[1:]
    if sub == "tail":
        n = 20
        if rest:
            try:
                n = int(rest[0])
            except ValueError:
                n = -1
            if n < 1:
                print(f"kb audit tail: N must be a positive number, got {rest[0]!r}")
                return 1
        lines = _raw_lines(root)
        if not lines:
            print("No audit entries yet (.athena/audit.jsonl).")
            return 0
        for line in lines[-n:]:
            print(_fmt(line))
        return 0
    if sub == "grep":
        if not rest or not " ".join(rest).strip():
            print("Usage: kb audit grep <text>")
            return 1
        needle = " ".join(rest).lower()
        hits = [l for l in _raw_lines(root) if needle in l.lower() or needle in _fmt(l).lower()]
        for line in hits:
            print(_fmt(line))
        return 0 if hits else 1
    if sub == "record":
        if len(rest) != 4 or rest[2] not in ("allow", "deny"):
            print("Usage: kb audit record <tool> <summary> <allow|deny> <reason>")
            return 1
        import audit
        return 0 if audit.record(root, rest[0], rest[1], rest[2], rest[3]) else 1
    print(USAGE)
    return 1
