#!/usr/bin/env python3
"""arcus-health: can this machine's arcus actually capture? (hourly sync preflight)

From about 2026-10 to 2026-11-22 the Max had no Playwright Chromium, so every
arcus browser capture failed and the kb quietly fell back; nobody knew for two
months. This runs before build:all in scripts/sync.sh and answers in seconds:

  1. launch arcus's browser once and fetch a page from a loopback server this
     script starts, through arcus's URL guard with a policy that allows only
     that server (so nothing leaves the machine);
  2. import yt_dlp (video captures).

It writes <vault>/.athena/state/arcus-health.json:
    {"ok": true|false, "reason": "...", "at": "<UTC ISO>", "versions": {...}}
and exits 0 when healthy, 1 when not. PGM's backstop reads the file.

    python3 scripts/arcus-health.py [--vault DIR]
"""
from __future__ import annotations

import argparse
import http.server
import importlib
import importlib.metadata
import json
import os
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent
LIB = ROOT / "bin" / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

MARKER = "athena-arcus-health-ok"
PAGE = (f"<html><head><title>health</title></head><body><p>{MARKER}</p>"
        "<p>" + "arcus health check paragraph. " * 20 + "</p></body></html>").encode()


class _Page(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(PAGE)))
        self.end_headers()
        self.wfile.write(PAGE)

    def log_message(self, *a):
        pass


def _origin_only_policy(port: int):
    """A guard (raise to refuse) that allows only http://127.0.0.1:<port>."""
    def policy(url: str) -> None:
        p = urlsplit(url)
        if not (p.scheme == "http" and p.hostname == "127.0.0.1" and p.port == port):
            raise PermissionError(f"health check fetches only its own loopback page, not {url}")
    return policy


def check_browser() -> str:
    """"" if arcus launched its browser and read the loopback page, else why not."""
    from arcus_errors import one_line
    try:
        from arcus.provider_runtime import set_url_guard
        from arcus.provider_runtime.providers.html._athena_fetch_page import fetch_page
    except ImportError as e:
        return one_line(f"arcus not importable: {e}")
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Page)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]
    try:
        set_url_guard(_origin_only_policy(port))
        try:
            text = fetch_page(f"http://127.0.0.1:{port}/") or ""
        except Exception as e:  # noqa: BLE001 — the reason is the result
            return one_line(f"browser fetch failed: {type(e).__name__}: {e}")
        finally:
            set_url_guard(None)
        if MARKER not in text:
            return f"browser fetch returned no page text ({len(text)} chars)"
        return ""
    finally:
        srv.shutdown()
        srv.server_close()


def check_ytdlp() -> str:
    from arcus_errors import one_line
    try:
        importlib.import_module("yt_dlp")
    except Exception as e:  # noqa: BLE001
        return one_line(f"yt_dlp not importable: {type(e).__name__}: {e}")
    return ""


def _versions() -> dict:
    out = {"python": sys.version.split()[0], "interpreter": sys.executable}
    for dist in ("arcus-provider-runtime", "playwright", "yt-dlp"):
        try:
            out[dist] = importlib.metadata.version(dist)
        except importlib.metadata.PackageNotFoundError:
            out[dist] = None
    return out


def write_state(vault: Path, result: dict) -> Path:
    state = vault / ".athena" / "state"
    state.mkdir(parents=True, exist_ok=True)
    path = state / "arcus-health.json"
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)  # a reader never sees a half-written file
    return path


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--vault", default=str(ROOT))
    args = ap.parse_args(argv)
    reasons = [r for r in (check_browser(), check_ytdlp()) if r]
    result = {
        "ok": not reasons,
        "reason": "; ".join(reasons),
        "at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "versions": _versions(),
    }
    path = write_state(Path(args.vault), result)
    if reasons:
        print(f"arcus-health: NOT OK: {result['reason']} (written to {path})", file=sys.stderr)
        return 1
    print(f"arcus-health: ok ({path})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
