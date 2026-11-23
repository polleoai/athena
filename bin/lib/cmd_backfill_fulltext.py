"""`kb backfill-fulltext [--page <stem>] [--limit N] [--dry-run] [--yes]`

Restore the paper text that arcus <= 0.8.1 cut at 50,000 characters
(arcus#18; fixed in arcus 0.8.2). For each paper raw with a local PDF, the
PDF is re-extracted and, if the raw's text section is a cut-off start of it,
the missing tail is APPENDED to that section (before a closing "## Figures"
section, which kb backfill-figures may have added). Nothing existing moves
or changes, figure lines included, and wiki pages are untouched.

The whole old text (figure lines set aside) must be found verbatim in the
new extraction; otherwise the raw is skipped and listed. One snapshot batch per run (kb undo), audited. The user approved
running this without a prompt per batch (2026-11-22); --yes skips the
Proceed? question, which is asked otherwise.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import List

_SECTION_RE = re.compile(r"^## (?:Full Text|Content)\s*$", re.M)
_FIG_LINE_RE = re.compile(r"^!\[[^\]]*\]\(\.\./\.\./assets/[^)]+\)\s*$")


def _text_span(raw: str) -> tuple[int, int] | None:
    """(start, end) of the raw's extracted-text section body: after its
    "## Full Text" / "## Content" heading, up to a closing "## Figures"
    section or the end of the file."""
    m = None
    for m in _SECTION_RE.finditer(raw):
        pass  # the last such heading is the text section
    if m is None:
        return None
    start = m.end()
    fig = raw.find("\n## Figures\n", start)
    return start, (fig if fig != -1 else len(raw))


def plan_repair(raw: str, new_text: str) -> tuple[str, str]:
    """(status, new_raw). status: "repaired", "whole", "no-section", "no-anchor"."""
    span = _text_span(raw)
    if span is None:
        return "no-section", raw
    start, end = span
    body = raw[start:end]
    plain = "\n".join(ln for ln in body.split("\n") if not _FIG_LINE_RE.match(ln)).strip()
    if not plain.strip():
        return "no-section", raw
    # The WHOLE old text (figure lines set aside) must appear verbatim in the
    # new extraction, not just its end: a raw that was edited, or extracted
    # differently, is skipped and listed rather than patched by guesswork.
    at = new_text.find(plain)
    if at == -1:
        return "no-anchor", raw
    missing = new_text[at + len(plain):].rstrip()
    if len(missing.strip()) < 1:
        return "whole", raw
    insert_at = start + len(body.rstrip())
    return "repaired", raw[:insert_at] + missing + raw[insert_at:]


def _pure_insertion(old: str, new: str) -> bool:
    """`new` is `old` with text inserted at one point: everything before and
    after it byte-identical. (A line-by-line check is wrong here: the 50,000-
    char cut lands mid-line, and the repair rightly extends that line.)"""
    if len(new) <= len(old):
        return False
    i = 0
    while i < len(old) and old[i] == new[i]:
        i += 1
    return new.endswith(old[i:])


def handle(argv: List[str], root: str) -> int:
    KB = os.path.realpath(os.path.abspath(root))
    sys.path.insert(0, os.path.join(KB, "bin", "lib"))
    import arcus_file  # noqa: E402
    import audit  # noqa: E402
    from cmd_backfill_figures import local_pdf  # noqa: E402
    from snapshot import snapshot_files  # noqa: E402

    args = list(argv)
    dry = "--dry-run" in args
    yes = "--yes" in args or "-y" in args
    page = args[args.index("--page") + 1] if "--page" in args else None
    limit = int(args[args.index("--limit") + 1]) if "--limit" in args else None

    raws = sorted(Path(KB, "raw", "papers", "artifacts").glob("*.md"))
    if page:
        raws = [r for r in raws if r.stem == page]
    todo = [(r, local_pdf(r)) for r in raws]
    no_pdf = [r.stem for r, p in todo if not p]
    todo = [(r, p) for r, p in todo if p]
    print(f"{len(raws)} papers: {len(todo)} with a local PDF, {len(no_pdf)} skipped (no local PDF)")
    if limit is not None:
        todo = todo[:limit]
    if not todo:
        print("Nothing to do.")
        return 0
    print(f"This run: {len(todo)} paper(s): " + ", ".join(r.stem for r, _ in todo))
    if dry:
        print("(dry run: nothing changed)")
        return 0
    if not yes:
        print("Missing text is appended to these raws in place (kb undo reverts the run). Proceed? (y/N)")
        try:
            answer = input().strip().lower()
        except EOFError:
            answer = ""
        if answer not in ("y", "yes"):
            audit.record(KB, "kb backfill-fulltext", f"{len(todo)} papers", "deny", "user-cancelled")
            print("Cancelled.")
            return 0

    staged, whole, skipped = [], [], []
    for r, pdf in todo:
        old = r.read_text(encoding="utf-8")
        print(f"  extracting {r.stem} ...", flush=True)
        try:
            new_text = arcus_file._extract_via_arcus(pdf, "pdf")["text"] or ""
        except Exception as e:  # noqa: BLE001 — one paper's failure doesn't stop the run
            skipped.append(f"{r.stem}: extraction failed: {type(e).__name__}: {e}")
            continue
        status, new = plan_repair(old, new_text)
        if status == "whole":
            whole.append(r.stem)
        elif status == "repaired" and _pure_insertion(old, new):
            staged.append((r, new, len(new) - len(old)))
        elif status == "repaired":
            skipped.append(f"{r.stem}: the repaired text would not keep every original line")
        else:
            skipped.append(f"{r.stem}: {status} (old text not found verbatim in the new extraction)"
                           if status == "no-anchor" else f"{r.stem}: no text section")
    if staged:
        snapshot_files(KB, files_to_delete=[], files_to_modify=[str(r) for r, *_ in staged],
                       operation="backfill-fulltext",
                       description=f"kb backfill-fulltext ({len(staged)} papers)",
                       reason="--yes" if yes else "user-confirmed")
        for r, new, _n in staged:
            r.write_text(new, encoding="utf-8")
    for r, _new, n in staged:
        print(f"  {r.stem}: +{n:,} chars")
    if whole:
        print(f"  already whole: {', '.join(whole)}")
    for line in skipped:
        print(f"  SKIPPED {line}")
    print(f"Done: papers repaired: {len(staged)}, chars restored: {sum(n for *_, n in staged):,}, "
          f"already whole: {len(whole)}, skipped: {len(skipped)}")
    if staged:
        print("  `kb undo` reverts this run. Wiki pages were not changed.")
    return 1 if skipped else 0
