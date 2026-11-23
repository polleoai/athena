"""Audit trail of destructive kb operations (Athena dev issue #180).

One JSON line per decision in <vault>/.athena/audit.jsonl, written by the
choke points every destructive path already goes through (snapshot.py:
snapshot_files / trash_files, restore_last, purge_old) and by the commands
that prompt, when the user declines (decision "deny").

Field names are Gryphon's DecisionAuditEntry (packages/protect/src/index.ts:
ts in epoch ms, tool, args_summary, decision, reason), plus `source`
("cli" for bin/kb, "plugin" for the Obsidian chat surface), so an analyzer
that reads Gryphon's audit reads Athena's. Extra keys (files, batch) ride
along where known.

The sink must never block or fail a kb command: any error prints one warning
to stderr and record() returns False. Past MAX_BYTES the log rolls to
audit.1.jsonl (newest) … audit.5.jsonl (oldest, then dropped).
"""
from __future__ import annotations

import json
import os
import sys
import time

AUDIT_DIR = ".athena"
AUDIT_FILE = "audit.jsonl"
MAX_BYTES = 10 * 1024 * 1024
KEEP = 5


def audit_path(kb_root: str) -> str:
    return os.path.join(kb_root, AUDIT_DIR, AUDIT_FILE)


def _rotate(path: str) -> None:
    base = path[: -len(".jsonl")]
    oldest = f"{base}.{KEEP}.jsonl"
    if os.path.exists(oldest):
        os.remove(oldest)
    for n in range(KEEP - 1, 0, -1):
        src = f"{base}.{n}.jsonl"
        if os.path.exists(src):
            os.replace(src, f"{base}.{n + 1}.jsonl")
    os.replace(path, f"{base}.1.jsonl")


def record(kb_root: str, tool: str, args_summary: str, decision: str,
           reason: str | None, source: str | None = None, default_reason: str | None = None,
           **extra) -> bool:
    """Append one decision. Returns False (after a stderr warning) when the
    sink fails; never raises. <source> defaults to $ATHENA_AUDIT_SOURCE
    (the plugin sets "plugin" when it runs bin/kb), else "cli". When the
    plugin runs a command the user confirmed in chat it sets
    $ATHENA_AUDIT_CONFIRMED, which replaces an allow's "--yes" or missing
    reason; <default_reason> fills a reason still missing."""
    try:
        source = source or os.environ.get("ATHENA_AUDIT_SOURCE") or "cli"
        confirmed = os.environ.get("ATHENA_AUDIT_CONFIRMED")
        if confirmed and decision == "allow" and reason in (None, "--yes"):
            reason = confirmed
        if reason is None:
            reason = default_reason
        entry = {"ts": int(time.time() * 1000), "tool": tool, "args_summary": args_summary,
                 "decision": decision, "reason": reason, "source": source, **extra}
        line = json.dumps(entry, ensure_ascii=False) + "\n"  # json escapes \n: one line
        path = audit_path(kb_root)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if os.path.exists(path) and os.path.getsize(path) + len(line.encode("utf-8")) > MAX_BYTES:
            _rotate(path)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(line)
        return True
    except Exception as exc:  # noqa: BLE001 — the sink must never break a kb command
        try:
            print(f"[athena] warning: audit log not written ({exc}); the command itself ran",
                  file=sys.stderr)
        except Exception:  # noqa: BLE001
            pass
        return False
