"""arcus_errors: why an arcus extraction failed, in one line.

The adapters run arcus with fds 1 and 2 silenced (its progress chatter would
corrupt their stdout protocol), so arcus's own message never reached a log.
On failure arcus still writes a stub `<slug>.json` with an `error` field;
this reads it. Witnessed 2026-11-22: "fetch_page raised: BrowserType.launch:
Executable doesn't exist ..." had been dropped on every browser capture since
about 2026-10 (no Playwright Chromium on the Max) and nobody saw it.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

MAX_CHARS = 300


def one_line(text: str) -> str:
    """The first non-empty line, the home dir shown as ~, capped."""
    for ln in str(text or "").splitlines():
        ln = ln.strip()
        if ln:
            home = os.path.expanduser("~")
            if home and home != "~":
                ln = ln.replace(home, "~")
            return ln if len(ln) <= MAX_CHARS else ln[:MAX_CHARS - 1] + "…"
    return ""


def failure_reason(out_dir: Path) -> str:
    """arcus's `error` from the stub .json in out_dir, as one line; "" if none."""
    try:
        stubs = sorted(Path(out_dir).glob("*.json"))
    except OSError:
        return ""
    for stub in stubs:
        try:
            data = json.loads(stub.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and data.get("error"):
            return one_line(data["error"])
    return ""


def with_reason(message: str, reason: str) -> str:
    return f"{message}: {reason}" if reason else message
