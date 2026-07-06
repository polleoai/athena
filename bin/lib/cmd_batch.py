"""`kb batch` -- process all URLs in inbox/url-new.txt by shelling each through
bin/kb-capture, then prune captured URLs from the inbox.

Ports the `batch)` arm of bin/kb-legacy. The capture step is the network path
(bin/kb-capture); only the empty-inbox surface ("No new URLs...") is
parity-tested. The rest (capture loop, TSV bookkeeping, inbox pruning) is a
faithful Python translation of the Bash, covered end-to-end by host/VM runs.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import time
from typing import List


def _frozen() -> bool:
    """True under a Nuitka/PyInstaller binary (no on-disk .py, no python exe)."""
    return getattr(sys, "frozen", False) or "__compiled__" in globals()


def _capture(root: str, url: str, desc: str) -> bool:
    """Run capture, mirroring the Bash arm. Returns success.

    Frozen binary: in-process via capture.run_capture() (no subprocess — the
    binary can't re-exec a .py). Source/dev mode keeps the subprocess call, so
    the test_kb_parity stub and Bash parity stay intact. See cmd_add._capture.
    """
    extra = ["--desc", desc] if desc else []
    if _frozen():
        import capture  # noqa: E402 — lazy so source mode never imports it
        sys.stdout.flush()
        return capture.run_capture(url, *extra, vault_root=root) == 0
    capture = os.path.join(root, "bin", "kb-capture")
    # kb-capture is now cross-platform Python — run via the interpreter (no
    # "/bin/bash", which ENOENT'd on native Windows). See cmd_add._capture.
    cmd = [sys.executable, capture, url, *extra]
    # Flush our own buffered stdout first so the child's inherited-fd writes
    # interleave in the same order Bash produced (Bash had no buffer between
    # its `echo "$url"` and the kb-capture child). Without this, our buffered
    # prints (the separator + URL line) land AFTER the child's immediate
    # writes when stdout is a pipe (not a TTY) -- diverging from the Bash arm.
    # Mirrors cmd_add._capture.
    sys.stdout.flush()
    # 2>&1: kb-capture's stderr is folded into the visible stream by the Bash
    # arm. We let both streams flow to the inherited fds (terminal), matching
    # the user-facing behavior.
    return subprocess.run(cmd, cwd=root).returncode == 0


def handle(argv: List[str], root: str) -> int:
    inbox = os.path.join(root, "inbox", "url-new.txt")
    if not os.path.isfile(inbox) or os.path.getsize(inbox) == 0:
        print("No new URLs in inbox/url-new.txt")
        return 0

    with open(inbox, "r", encoding="utf-8", errors="replace") as fh:
        raw_lines = fh.read().splitlines()

    # COUNT=$(grep -c -v '^[[:space:]]*$' "$INBOX")
    count = sum(1 for ln in raw_lines if not re.match(r"^[ \t]*$", ln))
    print(f"Processing {count} URLs from inbox/url-new.txt...")
    print()

    captured = 0
    failed = 0
    resolved = os.path.join(root, "inbox", "url-resolved.tsv")

    for line in raw_lines:
        # IFS=$'\t' read -r desc url rest
        parts = line.split("\t")
        desc = parts[0] if len(parts) > 0 else ""
        url = parts[1] if len(parts) > 1 else ""
        # [ -z "${url:-}" ] && url="$desc" && desc=""
        if not url:
            url = desc
            desc = ""
        # url=$(echo "$url" | tr -d '[:space:]')
        url = re.sub(r"\s", "", url)
        if not url:
            continue

        print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        print(f"{desc + ' — ' if desc else ''}{url}")

        if _capture(root, url, desc):
            captured += 1
            ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            with open(resolved, "a", encoding="utf-8") as fh:
                fh.write(f"captured\t\t{url}\t{ts}\n")
        else:
            failed += 1
        print()
        time.sleep(1)

    # Clear only successfully captured URLs from inbox.
    if failed == 0:
        open(inbox, "w").close()
    else:
        kept: List[str] = []
        try:
            with open(resolved, "r", encoding="utf-8", errors="replace") as fh:
                resolved_text = fh.read()
        except (IOError, OSError):
            resolved_text = ""
        for line in raw_lines:
            parts = line.split("\t")
            d = parts[0] if len(parts) > 0 else ""
            u = parts[1] if len(parts) > 1 else ""
            if not u:
                u = d
                d = ""
            u_clean = re.sub(r"\s", "", u)
            if not u_clean:
                continue
            # grep -q "captured.*${u_clean}" — captured anywhere on a line that
            # also contains the URL.
            if not re.search(r"captured.*" + re.escape(u_clean), resolved_text):
                # echo "${d}\t${u_clean}" — note: Bash `echo` here emits the
                # literal backslash-t (no -e), matching the original.
                kept.append(f"{d}\\t{u_clean}")
        with open(inbox, "w", encoding="utf-8") as fh:
            for ln in kept:
                fh.write(ln + "\n")
        print(f"Kept {failed} failed URLs in inbox/url-new.txt for retry.")

    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print(f"Batch complete: {captured} captured, {failed} failed")
    return 0
