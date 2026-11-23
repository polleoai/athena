"""`kb backfill-figures [--page <stem>] [--limit N] [--dry-run] [--yes]`

Give papers captured before arcus 0.8.0 their PDF figures, in place.

For each raw/papers/artifacts/<stem>.md with a LOCAL PDF (its "**PDF:**" or
"**PDF file:**" line, or <stem>.pdf beside it), arcus extracts the figures
into a temp dir; on apply each one is copied to raw/assets/<stem>/ and
inserted into the raw just before its caption line (unmatched ones go to a
closing "## Figures" section), with a "**Figures:** N kept, M skipped" line.
Nothing in the raw is removed or reordered, and wiki pages are not touched:
15 of the 53 paper pages carried later edits that kb delete + kb add would
have dropped (the user chose this command over that, 2026-11-22).

One snapshot batch per run (raws as modified, asset dirs as created), so
`kb undo` reverts the whole run. Asks Proceed? unless --yes.
"""
from __future__ import annotations

import glob
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path
from typing import List

_PDF_LINE = re.compile(r"^- \*\*PDF(?: file)?:\*\*\s*\./(\S+\.pdf)\s*$", re.M)
_FETCHED_LINE = re.compile(r"^- \*\*Content fetched:\*\*.*$", re.M)


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^0-9a-z]+", " ", (s or "").lower())).strip()


def local_pdf(raw: Path) -> Path | None:
    text = raw.read_text(encoding="utf-8", errors="replace")
    names = [m.group(1) for m in _PDF_LINE.finditer(text)] + [raw.stem + ".pdf"]
    for n in names:
        p = (raw.parent / n).resolve()
        if p.parent == raw.parent.resolve() and p.is_file():
            return p
    return None


def _alt(caption, n: int) -> str:
    c = re.sub(r"[\[\]\n\r]", " ", caption or "").strip()
    c = re.sub(r"\s+", " ", c)
    return (c[:117] + "…") if len(c) > 120 else (c or f"Figure {n}")


def insert_figures(text: str, kept: list, stem: str) -> str:
    """Insert `![caption](../../assets/<stem>/<file>)` before each figure's
    caption line; the rest go to a closing "## Figures" section. Grow-only."""
    lines = text.split("\n")
    used: set[int] = set()
    before: dict[int, list[str]] = {}
    unmatched: list[tuple[int, dict]] = []
    by_key: dict[str, int] = {}
    for n, fig in enumerate(kept, 1):
        ref = f"![{_alt(fig.get('caption'), n)}](../../assets/{stem}/{fig['local']})"
        key = _norm(fig.get("caption") or "")[:40]
        hit = None
        if len(key) >= 12:
            hit = by_key.get(key)  # panels of one figure share its caption line
            if hit is None:
                for i, ln in enumerate(lines):
                    if i not in used and _norm(ln).startswith(key):
                        hit = i
                        break
            if hit is not None:
                by_key[key] = hit
        if hit is None:
            unmatched.append((n, fig))
            continue
        used.add(hit)
        before.setdefault(hit, []).extend([ref, ""])
    out: list[str] = []
    for i, ln in enumerate(lines):
        out.extend(before.get(i, []))
        out.append(ln)
    if unmatched:
        while out and out[-1] == "":
            out.pop()
        out += ["", "## Figures", ""]
        for n, fig in unmatched:
            out += [f"![{_alt(fig.get('caption'), n)}](../../assets/{stem}/{fig['local']})", ""]
            if fig.get("caption"):
                out += [fig["caption"], ""]
    return "\n".join(out).rstrip("\n") + "\n"


def _grow_only(old: str, new: str) -> bool:
    it = iter(new.splitlines())
    return len(new) > len(old) and all(any(o == n for n in it) for o in old.splitlines())


def _add_figures_line(text: str, note: str) -> str:
    """After the "Content fetched" line; without one, right after the
    frontmatter (a raw always gets the line, so a re-run can see it)."""
    if not note:
        return text
    m = _FETCHED_LINE.search(text)
    if m:
        return text[:m.end()] + f"\n- **Figures:** {note}" + text[m.end():]
    end = text.find("\n---\n", 3) if text.startswith("---") else -1
    at = end + len("\n---\n") if end != -1 else 0
    return text[:at] + f"\n- **Figures:** {note}\n" + text[at:]


def raw_groups(KB: str) -> dict[str, list[str]]:
    """stem -> the ordered stems of its page's paper raws, for pages that list
    more than one (a merged page, or kb merge-raw). One paper, one set of
    figures: only the group's carrier gets them."""
    from cmd_merge_raw import _page_raws, _split_fm
    groups: dict[str, list[str]] = {}
    for w in glob.glob(os.path.join(KB, "wiki", "**", "*.md"), recursive=True):
        try:
            fm, _ = _split_fm(Path(w).read_text(encoding="utf-8", errors="replace")[:4000])
        except OSError:
            continue
        stems = [Path(r).stem for r in _page_raws(fm or "") if r.startswith("raw/papers/artifacts/")]
        if len(stems) > 1:
            for st in stems:
                groups.setdefault(st, stems)
    return groups


def _carrier(group: list[str], art: Path) -> str | None:
    """The member that has, or will get, the group's figures: one that
    already has them, else the first (raw_paths order) with a local PDF."""
    for st in group:
        f = art / f"{st}.md"
        if f.is_file() and f"../../assets/{st}/figure-" in f.read_text(encoding="utf-8", errors="replace"):
            return st
    for st in group:
        f = art / f"{st}.md"
        if f.is_file() and local_pdf(f):
            return st
    return None


def handle(argv: List[str], root: str) -> int:
    KB = os.path.abspath(root)
    sys.path.insert(0, os.path.join(KB, "bin", "lib"))
    import arcus_file  # noqa: E402
    import audit  # noqa: E402
    from snapshot import mark_created, snapshot_files  # noqa: E402

    args = list(argv)
    dry = "--dry-run" in args
    yes = "--yes" in args or "-y" in args
    page = args[args.index("--page") + 1] if "--page" in args else None
    limit = int(args[args.index("--limit") + 1]) if "--limit" in args else None

    raws = sorted(Path(KB, "raw", "papers", "artifacts").glob("*.md"))
    if page:
        raws = [r for r in raws if r.stem == page]
        if not raws:
            print(f"No paper raw found for: {page}")
            return 1
    todo, no_pdf, done_already, same_paper = [], [], [], []
    art = Path(KB, "raw", "papers", "artifacts")
    groups = raw_groups(KB)
    for r in raws:
        if r.stem in groups:
            c = _carrier(groups[r.stem], art)
            if c and c != r.stem:
                same_paper.append(f"{r.stem} (same paper as {c})")
                continue
        text = r.read_text(encoding="utf-8", errors="replace")
        # Processed before: figures embedded, or a "**Figures:**" line (a
        # paper with none to keep is marked too, so it isn't re-extracted).
        if f"../../assets/{r.stem}/figure-" in text or re.search(r"^- \*\*Figures:\*\*", text, re.M):
            done_already.append(r.stem)
            continue
        pdf = local_pdf(r)
        (todo.append((r, pdf)) if pdf else no_pdf.append(r.stem))
    print(f"{len(raws)} papers: {len(todo) + len(done_already)} with a local PDF, "
          f"{len(no_pdf)} skipped (no local PDF), {len(done_already)} already have figures")
    if no_pdf:
        print("  skipped (no local PDF): " + ", ".join(no_pdf))
    if same_paper:
        print("  skipped, figures go on another raw of the same page: " + ", ".join(same_paper))
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
        print("Figures are added to these raws in place (kb undo reverts the run). Proceed? (y/N)")
        try:
            answer = input().strip().lower()
        except EOFError:
            answer = ""
        if answer not in ("y", "yes"):
            audit.record(KB, "kb backfill-figures", f"{len(todo)} papers", "deny", "user-cancelled")
            print("Cancelled.")
            return 0
    reason = "--yes" if yes else "user-confirmed"

    staged, failed, no_figs = [], [], []
    tmp = Path(tempfile.mkdtemp(prefix="athena-backfill-figures-"))
    try:
        for r, pdf in todo:
            old = r.read_text(encoding="utf-8")
            if Path(KB, "raw", "assets", r.stem).exists():
                failed.append(f"{r.stem}: raw/assets/{r.stem}/ already exists")
                continue
            print(f"  extracting {r.stem} ...", flush=True)
            try:
                res = arcus_file._extract_via_arcus(pdf, "pdf", vault=tmp, asset_slug=r.stem)
            except Exception as e:  # noqa: BLE001 — one paper's failure doesn't stop the run
                failed.append(f"{r.stem}: {type(e).__name__}: {e}")
                continue
            kept, skipped = res.get("figures") or [], res.get("figures_skipped") or []
            if not kept:
                note = arcus_file.figures_note(kept, skipped) or "0 kept"
                marked = _add_figures_line(old, note)
                if marked != old:  # mark it processed; grow-only like the rest
                    staged.append((r, marked, 0, len(skipped)))
                    no_figs.append(f"{r.stem} ({note}): no figures kept; marked processed")
                else:
                    no_figs.append(f"{r.stem} ({note}): no figures kept; could NOT mark it (re-runs will retry)")
                continue
            new = insert_figures(old, kept, r.stem)
            new = _add_figures_line(new, arcus_file.figures_note(kept, skipped))
            if not _grow_only(old, new):
                failed.append(f"{r.stem}: the new text would not keep every original line; left as is")
                continue
            staged.append((r, new, len(kept), len(skipped)))
        if staged:
            batch = snapshot_files(KB, files_to_delete=[], files_to_modify=[str(r) for r, *_ in staged],
                                   operation="backfill-figures",
                                   description=f"kb backfill-figures ({len(staged)} papers)", reason=reason)
            for r, new, kept_n, _s in staged:
                if kept_n:  # a "0 kept" mark has no asset dir to move
                    dest = Path(KB, "raw", "assets", r.stem)
                    shutil.move(str(tmp / "raw" / "assets" / r.stem), str(dest))
                    mark_created(KB, batch, str(dest))
                r.write_text(new, encoding="utf-8")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    for r, _new, k, s in staged:
        if k:
            print(f"  {r.stem}: {k} figure(s) added" + (f", {s} skipped" if s else ""))
    for line in no_figs:
        print(f"  {line}")
    for line in failed:
        print(f"  FAILED {line}")
    print(f"Done: papers done: {sum(1 for *_, k, _s in staged if k)}, figures added: {sum(k for *_, k, _s in staged)}, "
          f"figures skipped: {sum(s for *_, s in staged)}, papers without figures: {len(no_figs)}, "
          f"failed: {len(failed)}")
    if staged:
        print("  `kb undo` reverts this run. Wiki pages were not changed.")
    return 1 if failed else 0
