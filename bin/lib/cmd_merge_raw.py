"""`kb merge-raw <twin raw> --into "<page>" [--yes]`

Fold a page-less twin raw into the wiki page that already covers the same
source. kb merge merges PAGES; this is for a raw that has no page of its own
(5 arXiv papers had one, 2026-11-22). It does to the twin what kb merge does
to a loser's raw:

  * the page's `raw_path:` becomes `raw_paths:` with the twin appended (or
    the twin is appended to an existing `raw_paths:` list);
  * the twin gets `merged_into: "<page>"` (lint's orphan pass then leaves it
    alone, and its full text stays reachable from the page).

No body changes. One snapshot batch (kb undo restores both files), an audit
entry, and Proceed? unless --yes. Refuses a twin with a different source.
"""
from __future__ import annotations

import glob
import os
import re
import sys
from pathlib import Path
from typing import List

_RAWPATH_RE = re.compile(r'^raw_path:\s*"?([^"\n]+?)"?\s*$', re.M)
_SOURCE_RE = re.compile(r'^source:\s*"?([^"\n]+?)"?\s*$', re.M)


def _split_fm(text: str):
    if not text.startswith("---"):
        return None, text
    end = text.find("\n---", 3)
    if end == -1:
        return None, text
    return text[:end], text[end:]  # fm without closing line; rest starts "\n---"


def _page_raws(fm: str) -> list[str]:
    out = [m.group(1).strip() for m in _RAWPATH_RE.finditer(fm)]
    lm = re.search(r'^raw_paths:\s*\n((?:\s+-\s+.*\n?)+)', fm + "\n", re.M)
    if lm:
        out += [re.sub(r'^\s+-\s+', "", ln).strip().strip('"').strip("'")
                for ln in lm.group(1).splitlines() if ln.strip()]
    return out


def _resolve_raw(KB: str, arg: str) -> Path | None:
    cands = [Path(KB, arg)] if "/" in arg else []
    stem = Path(arg).stem
    cands += [Path(p) for p in glob.glob(os.path.join(KB, "raw", "*", "artifacts", stem + ".md"))]
    root = Path(KB).resolve()
    for c in cands:
        c = c.resolve()
        if c.is_file() and c.suffix == ".md" and c.is_relative_to(root / "raw"):
            return c
    return None


def handle(argv: List[str], root: str) -> int:
    KB = os.path.realpath(os.path.abspath(root))  # resolved, like the raw paths below
    sys.path.insert(0, os.path.join(KB, "bin", "lib"))
    import audit  # noqa: E402
    from snapshot import snapshot_files  # noqa: E402
    from url_canonical import canonicalize  # noqa: E402

    args = list(argv)
    yes = "--yes" in args or "-y" in args
    args = [a for a in args if a not in ("--yes", "-y")]
    if "--into" not in args or args.index("--into") == 0 or args.index("--into") + 1 >= len(args):
        print('Usage: kb merge-raw <twin raw> --into "<page>" [--yes]')
        return 2
    i = args.index("--into")
    twin_arg, page_name = " ".join(args[:i]), " ".join(args[i + 1:])

    twin = _resolve_raw(KB, twin_arg)
    if twin is None:
        print(f"Raw not found: {twin_arg}")
        return 1
    # glob.escape: page names carry [ ] (e.g. "arXiv: [2605.00055] ...").
    pages = glob.glob(os.path.join(glob.escape(os.path.join(KB, "wiki")), "**",
                                   glob.escape(f"{page_name}.md")), recursive=True)
    if not pages:
        print(f"Page not found: {page_name}")
        return 1
    page = Path(pages[0])
    twin_rel = os.path.relpath(twin, KB).replace(os.sep, "/")

    page_text = page.read_text(encoding="utf-8")
    page_fm, page_rest = _split_fm(page_text)
    if page_fm is None:
        print(f"Page has no frontmatter: {page_name}")
        return 1
    raws = _page_raws(page_fm)
    if twin_rel in raws:
        print(f"{twin_rel} is already on [[{page_name}]]; nothing to merge")
        return 1
    twin_text = twin.read_text(encoding="utf-8")
    twin_fm, twin_rest = _split_fm(twin_text)
    if twin_fm is None:
        print(f"Raw has no frontmatter: {twin_rel}")
        return 1
    if re.search(r"^merged_into:", twin_fm, re.M):
        print(f"{twin_rel} is already merged into another page")
        return 1

    def canon(u: str) -> str:
        try:
            return canonicalize(u).url
        except Exception:  # noqa: BLE001
            return u.strip()
    tsrc = _SOURCE_RE.search(twin_fm)
    page_srcs = set()
    for r in raws:
        try:
            rfm, _ = _split_fm(Path(KB, r).read_text(encoding="utf-8"))
        except OSError:
            continue
        m = _SOURCE_RE.search(rfm or "")
        if m:
            page_srcs.add(canon(m.group(1)))
    if not tsrc or canon(tsrc.group(1)) not in page_srcs:
        print(f"Refusing: {twin_rel} has a different source than [[{page_name}]]'s raws "
              f"({tsrc.group(1) if tsrc else 'none'} vs {sorted(page_srcs) or 'none'})")
        return 1

    # New page frontmatter: one raw_paths list, the twin last.
    new_list = raws + [twin_rel]
    fm_wo = _RAWPATH_RE.sub("", page_fm)
    fm_wo = re.sub(r'^raw_paths:\s*\n(?:\s+-\s+.*\n?)+', "", fm_wo + "\n", flags=re.M).rstrip("\n")
    fm_wo = re.sub(r"\n{2,}", "\n", fm_wo)
    block = "raw_paths:\n" + "\n".join(f'  - "{r}"' for r in new_list)
    lines = fm_wo.split("\n")
    at = next((k + 1 for k, ln in enumerate(lines) if ln.startswith("source_type:")), 1)
    lines[at:at] = block.split("\n")
    new_page = "\n".join(lines) + page_rest
    new_twin = twin_fm + f'\nmerged_into: "{page_name}"' + twin_rest

    print(f"Merge {twin_rel} into [[{page_name}]]:")
    print(f"  page raw_paths: {', '.join(new_list)}")
    print(f'  {twin_rel}: merged_into: "{page_name}"   (no text changes; kb undo reverts)')
    if not yes:
        print("Proceed? (y/N)")
        try:
            answer = input().strip().lower()
        except EOFError:
            answer = ""
        if answer not in ("y", "yes"):
            audit.record(KB, "kb merge-raw", f"{twin_rel} -> {page_name}", "deny", "user-cancelled")
            print("Cancelled.")
            return 0
    snapshot_files(KB, files_to_delete=[], files_to_modify=[str(page), str(twin)],
                   operation="merge-raw", description=f'kb merge-raw {twin_rel} --into "{page_name}"',
                   reason="--yes" if yes else "user-confirmed")
    page.write_text(new_page, encoding="utf-8")
    twin.write_text(new_twin, encoding="utf-8")
    print("Done. `kb undo` reverts it.")
    return 0
