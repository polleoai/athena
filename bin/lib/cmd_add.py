"""`kb add [<url|file> ...]` -- the central ingest arm.

Ports the `add)` arm of bin/kb-legacy (the largest arm). Three top-level shapes,
each a faithful Python translation of the Bash:

  * NO ARGS  -> process all pending sources: inbox/url-new.txt URLs (network,
    via bin/kb-capture), then Web-Clipper files sitting in inbox/Clippings/
    (LOCAL, no network -- the 03-inbox path), then a dead-page sweep, then
    orphan-raw -> wiki-page creation, then "Nothing to process"/URL-Tracker
    bookkeeping. Several embedded heredocs are lifted verbatim into sibling
    _add_*_body.py files and run via _heredoc_runner so module-scope globals
    behave identically.
  * LOCAL FILE (first arg is an existing file) -> ingest_local_file + wiki page.
  * URLs -> capture each via bin/kb-capture, then build a wiki page from the
    newest raw; optionally chase a referenced paper.

Network steps (bin/kb-capture, bulk-llm-regen synthesis) are not parity-tested
here -- the deterministic surfaces (empty inbox, unsupported file type) are; full
inbox/URL flows are covered by the host/VM 03-inbox spec. The capture step still
shells into bin/kb-capture (Bash) exactly as the legacy arm did.
"""
from __future__ import annotations

import glob
import os
import re
import subprocess
import sys
import time
from typing import List

from _heredoc_runner import run_body


def _frozen() -> bool:
    """True under a Nuitka/PyInstaller binary (no on-disk .py, no python exe)."""
    return getattr(sys, "frozen", False) or "__compiled__" in globals()


def _capture(root: str, *args: str) -> int:
    """Run capture with the given args (mirrors `"${KB_ROOT}/bin/kb-capture" ...`).

    Frozen binary: call capture.run_capture() IN-PROCESS — sys.executable is the
    binary itself and bin/kb-capture is not on disk, so the subprocess form
    cannot work. Source/dev mode keeps the subprocess call byte-for-byte, which
    preserves the Bash parity oracle and the test_kb_parity stub (a fake
    <vault>/bin/kb-capture the test injects on the subprocess boundary).
    """
    if _frozen():
        import capture  # noqa: E402 — lazy so source mode never imports it
        sys.stdout.flush()
        return capture.run_capture(*args, vault_root=root)
    capture = os.path.join(root, "bin", "kb-capture")
    # Flush our own buffered stdout first so the child's inherited-fd writes
    # interleave in the same order Bash produced (Bash had no buffer between
    # its echos and the child). Without this, our buffered prints land AFTER
    # the child's immediate writes.
    sys.stdout.flush()
    # kb-capture is now a cross-platform Python program; run it under the same
    # interpreter kb itself runs on (sys.executable == $ATHENA_PYTHON on a guest
    # whose stock python3 is too old). No more "/bin/bash" — that ENOENT'd on
    # native Windows (WinError 2), the last Bash dependency in URL ingest.
    return subprocess.run([sys.executable, capture, *args], cwd=root).returncode


def _rel(root: str, path: str) -> str:
    return os.path.relpath(path, root)


def _wiki_page(root: str, rel_raw: str, url: str = "") -> None:
    """Invoke bin/lib/wiki_page.py the same way the Bash arm did."""
    module = os.path.join(root, "bin", "lib", "wiki_page.py")
    args = [sys.executable, module, "--vault", root, "--raw-path", rel_raw]
    if url:
        args += ["--url", url]
    args += ["--source", "shell"]
    sys.stdout.flush()
    subprocess.run(args, cwd=root)


def _newest_raw(root: str) -> str:
    """Replicate `ls -t raw/*/*.md raw/*/artifacts/*.md | head -1`."""
    candidates = (glob.glob(os.path.join(root, "raw", "*", "*.md"))
                  + glob.glob(os.path.join(root, "raw", "*", "artifacts", "*.md")))
    if not candidates:
        return ""
    candidates.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    return candidates[0]


def _process_no_args(root: str) -> int:
    pending = 0

    # 1. inbox/url-new.txt (network)
    url_new = os.path.join(root, "inbox", "url-new.txt")
    if os.path.isfile(url_new) and os.path.getsize(url_new) > 0:
        with open(url_new, "r", encoding="utf-8", errors="replace") as fh:
            lines = fh.read().splitlines(keepends=True)
        url_count = sum(1 for ln in lines if "http" in ln)
        if url_count > 0:
            print(f"Found {url_count} URLs in inbox/url-new.txt")
            pending += url_count
            for ln in lines:
                url = re.sub(r"\s", "", ln)
                if not url:
                    continue
                if not url.startswith("http"):
                    continue
                print()
                print(f"── Processing: {url}")
                _capture(root, url)
            # Archive processed URLs.
            resolved = os.path.join(root, "inbox", "url-resolved.tsv")
            try:
                with open(url_new, "r", encoding="utf-8", errors="replace") as src, \
                     open(resolved, "a", encoding="utf-8") as dst:
                    dst.write(src.read())
            except (IOError, OSError):
                pass
            open(url_new, "w").close()
            print()
            print(f"Processed {url_count} URLs from inbox.")

    # 2. Clippings (local, no network) -- the 03-inbox path.
    clip_dirs: List[str] = []
    if os.path.isdir(os.path.join(root, "inbox", "Clippings")):
        clip_dirs.append(os.path.join(root, "inbox", "Clippings"))
    if os.path.isdir(os.path.join(root, "Clippings")):
        clip_dirs.append(os.path.join(root, "Clippings"))
    if os.path.isdir(os.path.join(root, "inbox", "clippings")):
        clip_dirs.append(os.path.join(root, "inbox", "clippings"))

    for clip_dir in clip_dirs:
        if not os.path.isdir(clip_dir):
            continue
        clips = sorted(
            p for p in glob.glob(os.path.join(clip_dir, "*.md"))
            if os.path.isfile(p)
        )
        clip_count = len(clips)
        if clip_count > 0:
            print(f"Found {clip_count} clipped pages in inbox/clippings/")
            pending += clip_count
            for clip in clips:
                if not clip:
                    continue
                clip_name = os.path.splitext(os.path.basename(clip))[0]
                print()
                print(f"── Processing clip: {clip_name}")
                ok, out = _run_process_clip(root, clip)
                if ok:
                    rel = _strip_root(out, root)
                    print(f"  Saved: {rel}")
                else:
                    print("  ✗ Clip processing failed:")
                    for line in out.splitlines():
                        print(f"    {line}")
                    # Don't move-to-processed on failure -- leave clip for retry.
                    continue
                # Move clip to processed.
                processed_dir = os.path.join(clip_dir, ".processed")
                os.makedirs(processed_dir, exist_ok=True)
                try:
                    os.replace(clip, os.path.join(processed_dir, os.path.basename(clip)))
                except OSError:
                    pass
            print()
            print(f"Processed {clip_count} clips from {os.path.basename(clip_dir)}/")

    # 2.5. Dead-page sweep.
    run_body("_add_dead_check_body.py", [], root)

    # 3. Orphan raws -> wiki pages (mtime-gated).
    max_age = os.environ.get("ATHENA_RECONCILE_MAX_AGE_MIN", "10")
    saved_env = {
        "RECONCILE_ALL": os.environ.get("RECONCILE_ALL"),
        "RECONCILE_MAX_AGE_MIN": os.environ.get("RECONCILE_MAX_AGE_MIN"),
    }
    os.environ["RECONCILE_ALL"] = os.environ.get("ATHENA_RECONCILE_ORPHANS", "")
    os.environ["RECONCILE_MAX_AGE_MIN"] = max_age
    orphan_out = _capture_stdout_body("_add_orphan_check_body.py", root)
    # Restore env exactly.
    for k, v in saved_env.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v

    orphan_list = [ln for ln in orphan_out.splitlines() if ln]
    if orphan_list:
        orphan_count = sum(1 for ln in orphan_list if ln.strip())
        print()
        print(f"Found {orphan_count} raw sources without wiki pages — creating now...")
        pending += orphan_count
        for rf in orphan_list:
            if not rf:
                continue
            print()
            print(f"── Creating wiki page: {os.path.splitext(os.path.basename(rf))[0]}")
            rel_raw = os.path.relpath(rf, root)
            _wiki_page(root, rel_raw)

        # synthesis-after-create (skipped when ATHENA_SKIP_SYNTH=1).
        bulk = os.path.join(root, "scripts", "bulk-llm-regen-summaries.py")
        if not os.environ.get("ATHENA_SKIP_SYNTH") and os.path.isfile(bulk):
            print()
            print("── Filling in summaries for newly-created pages...")
            proc = subprocess.run(
                [sys.executable, bulk, "--only-newer-than-mins", "30"],
                cwd=root, capture_output=True, text=True)
            tail = (proc.stdout + proc.stderr).splitlines()[-20:]
            for line in tail:
                print(line)

    if pending == 0:
        # Check if any URLs need user help.
        needs_help = 0
        resolved_tsv = os.path.join(root, "inbox", "url-resolved.tsv")
        if os.path.isfile(resolved_tsv):
            try:
                with open(resolved_tsv, "r", encoding="utf-8", errors="replace") as fh:
                    needs_help = sum(
                        1 for ln in fh
                        if ln.startswith("uncapturable") or ln.startswith("thin"))
            except (IOError, OSError):
                needs_help = 0
        if needs_help > 0:
            print(f"{needs_help} URLs need your help — see [[URL Tracker]]")
            print("  Click the link, use Web Clipper — Athena auto-ingests.")
        else:
            print("Nothing to process.")
            print("  - Clip pages in your browser with Web Clipper")
            print("  - Or: kb add <url>")

    # Reconcile tracking + regenerate dashboards.
    sys.path.insert(0, os.path.join(root, "bin", "lib"))
    from wiki_page import (reconcile_tracking, generate_url_tracker,  # noqa: E402
                           generate_page_index)
    reconcile_tracking(root)
    generate_url_tracker(root)
    generate_page_index(root)
    return 0


def _run_process_clip(root: str, clip: str) -> tuple[bool, str]:
    """Run the process_clip shim IN-PROCESS, capturing combined stdout+stderr
    (the Bash inline `python3 -c` captured 2>&1). Returns (success, output).

    In-process (vs the old `sys.executable -c`) so the handler compiles into a
    single binary with no re-exec; output stays byte-identical for the
    pure-Python clip path validated by tests/test_kb_parity.py."""
    import io
    from contextlib import redirect_stderr, redirect_stdout
    os.environ["KB_ROOT"] = root  # process_clip + any capture-deep hop read it
    from process_clip import ProcessClipError, process_clip
    out_buf, err_buf = io.StringIO(), io.StringIO()
    ok = True
    with redirect_stdout(out_buf), redirect_stderr(err_buf):
        try:
            print(process_clip(clip, root))
        except ProcessClipError as exc:
            sys.stderr.write(f"  {exc}\n")
            ok = False
    # Bash captured `2>&1` (stdout then stderr) and stripped the trailing \n.
    out = (out_buf.getvalue() + err_buf.getvalue()).rstrip("\n")
    return ok, out


def _strip_root(path: str, root: str) -> str:
    """Replicate `${RAW_FILE#${KB_ROOT}/}` -- strip a leading "<root>/" prefix."""
    prefix = root + "/"
    if path.startswith(prefix):
        return path[len(prefix):]
    return path


def _capture_stdout_body(body_filename: str, root: str) -> str:
    """Run a verbatim body as a child python process, capturing its stdout --
    the orphan-check heredoc's stdout was captured by `$(...)` in Bash."""
    body_path = os.path.join(root, "bin", "lib", body_filename)
    proc = subprocess.run(
        [sys.executable, body_path, root],
        cwd=root, capture_output=True, text=True, env=dict(os.environ))
    return proc.stdout


def _process_local_file(root: str, filepath: str) -> int:
    return run_body("_add_file_ingest_body.py", [filepath], root)


def _process_urls(root: str, argv: List[str]) -> int:
    urls: List[str] = []
    extra: List[str] = []
    for arg in argv:
        if arg.startswith("http://") or arg.startswith("https://"):
            urls.append(arg)
        else:
            extra.append(arg)

    before_raw = _newest_raw(root)

    capture_ok = False
    if len(urls) > 1:
        if extra:
            print("WARNING: --desc/--keywords/--category flags are ignored in multi-URL mode.")
            print("  To add metadata, capture URLs one at a time.")
            print()
        print(f"Adding {len(urls)} URLs...")
        for url in urls:
            print()
            print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
            _capture(root, url)
            time.sleep(1)
        print()
        print(f"Done. Added {len(urls)} sources.")
        capture_ok = True  # multi-URL can have partial failures; trust the loop
    else:
        if _capture(root, *argv) == 0:
            capture_ok = True

    last_raw = _newest_raw(root)
    if capture_ok and last_raw and last_raw != before_raw:
        rel_raw = os.path.relpath(last_raw, root)
        if len(urls) == 1 and urls[0]:
            _wiki_page(root, rel_raw, urls[0])
        else:
            _wiki_page(root, rel_raw)

    # Chase a referenced paper if kb-capture flagged one.
    pending_paper = os.path.join(root, ".athena", "_pending_paper_url")
    if os.path.isfile(pending_paper):
        try:
            with open(pending_paper, "r", encoding="utf-8", errors="replace") as fh:
                paper_url = fh.read().strip()
        except (IOError, OSError):
            paper_url = ""
        try:
            os.remove(pending_paper)
        except OSError:
            pass
        if paper_url:
            print()
            print(f"  Also capturing referenced paper: {paper_url}")
            _capture(root, paper_url)
            # `ls -t raw/*/*.md | head -1` (note: no artifacts glob here).
            paper_candidates = glob.glob(os.path.join(root, "raw", "*", "*.md"))
            paper_candidates.sort(key=lambda p: os.path.getmtime(p), reverse=True)
            paper_raw = paper_candidates[0] if paper_candidates else ""
            if paper_raw:
                run_body("_add_paper_wiki_body.py", [paper_raw], root)
    return 0


def handle(argv: List[str], root: str) -> int:
    if not argv:
        return _process_no_args(root)

    first = argv[0]
    if os.path.isfile(first):
        return _process_local_file(root, first)
    return _process_urls(root, argv)
