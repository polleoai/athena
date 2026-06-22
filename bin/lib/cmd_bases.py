"""`kb bases` — generate Obsidian Bases (.base YAML) over typed wiki collections.

For each non-empty typed collection under `wiki/format/<type>/` (papers, repos,
videos, webpages, images, entities, comparisons) this writes a per-collection
`wiki/bases/<Title>.base` plus a master `wiki/bases/All Knowledge.base` whose
views cover every non-empty collection. Empty collections are skipped (and
logged). Regeneration is idempotent: a second run with no vault change produces
byte-identical files; before overwriting an existing `.base`, the prior file is
snapshotted to `.kb-trash/<timestamp>_bases/` so nothing is lost.

The YAML is hand-emitted deterministically (no pyyaml dependency) so output is
stable across runs and platforms.
"""
from __future__ import annotations

import os
import time
from typing import List, Tuple

# Collection type -> (title-cased plural filename stem, column order).
# Column order is authoritative per the kb-views design spec: only the props a
# type actually carries. file.name is always first.
_COLLECTIONS: List[Tuple[str, str, List[str]]] = [
    ("papers", "Papers", ["file.name", "tags", "date_added", "url"]),
    ("repos", "Repos", ["file.name", "tags", "urls"]),  # repo pages store urls: (plural)
    ("videos", "Videos", ["file.name", "tags", "date_added", "url"]),
    ("webpages", "Webpages", ["file.name", "tags", "date_added", "url"]),
    ("images", "Images", ["file.name", "tags"]),
    ("entities", "Entities", ["file.name", "tags"]),
    ("comparisons", "Comparisons", ["file.name", "tags"]),
]

# Types that carry a date_added property get a default DESC sort on it.
_SORT_PROP = "date_added"


def _count_pages(folder: str) -> int:
    """Count *.md files in `folder`, excluding underscore-prefixed scaffolding
    (e.g. _Contents.md, _TEMPLATE.md)."""
    if not os.path.isdir(folder):
        return 0
    return sum(
        1
        for name in os.listdir(folder)
        if name.endswith(".md") and not name.startswith("_")
    )


def _per_collection_base(name: str, folder_rel: str, order: List[str]) -> str:
    """Build a single-collection .base: a top-level folder filter plus one view."""
    lines = [
        "filters:",
        "  and:",
        f"    - 'file.inFolder(\"{folder_rel}\")'",
        "views:",
    ]
    # Top-level filter already scopes the base; the view repeats it so the file
    # is self-contained and matches the All Knowledge per-view shape.
    lines += _view_for_master(name, folder_rel, order)
    return "\n".join(lines) + "\n"


def _view_for_master(name: str, folder_rel: str, order: List[str]) -> List[str]:
    """A views list-item at the top level (2-space dash indent).

    The list-item dash sits at column 2 (`  - type: table`), so `type` begins at
    column 4. Sibling keys (name, filters, order, sort) MUST align with `type` at
    column 4 — that is what makes the mapping valid YAML."""
    child = "    "  # keys under "  - " list item, aligned with `type`
    lines = [
        "  - type: table",
        f"{child}name: {name}",
        f"{child}filters:",
        f"{child}  and:",
        f"{child}    - 'file.inFolder(\"{folder_rel}\")'",
        f"{child}order:",
    ]
    lines += [f"{child}  - {col}" for col in order]
    if _SORT_PROP in order:
        lines += [
            f"{child}sort:",
            f"{child}  - property: {_SORT_PROP}",
            f"{child}    direction: DESC",
        ]
    return lines


def _master_base(active: List[Tuple[str, str, List[str]]]) -> str:
    """Build All Knowledge.base: one view per non-empty collection.

    `active` is a list of (display_name, folder_rel, order)."""
    lines = ["views:"]
    for name, folder_rel, order in active:
        lines += _view_for_master(name, folder_rel, order)
    return "\n".join(lines) + "\n"


def _snapshot_if_changed(path: str, new_content: str, root: str, stamp: str) -> None:
    """If `path` exists and its content differs from `new_content`, snapshot the
    prior file to `.kb-trash/<stamp>_bases/` before it is overwritten. No-op when
    the file is absent or already byte-identical (keeps idempotent reruns clean)."""
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as fh:
        if fh.read() == new_content:
            return
    trash_dir = os.path.join(root, ".kb-trash", f"{stamp}_bases")
    os.makedirs(trash_dir, exist_ok=True)
    with open(path, "r", encoding="utf-8") as src:
        prior = src.read()
    with open(os.path.join(trash_dir, os.path.basename(path)), "w", encoding="utf-8") as dst:
        dst.write(prior)


def _write(path: str, content: str, root: str, stamp: str) -> bool:
    """Write `content` to `path`, snapshotting any prior differing file first.
    Returns True if the file was written/changed, False if already identical."""
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as fh:
            if fh.read() == content:
                return False
    _snapshot_if_changed(path, content, root, stamp)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)
    return True


def handle(argv: List[str], root: str) -> int:
    for arg in argv:
        if arg in ("--help", "-h"):
            print("Usage: kb bases")
            print("")
            print("Generate Obsidian Bases (.base) over Athena's typed wiki")
            print("collections into wiki/bases/. One base per non-empty collection")
            print("(papers, repos, videos, webpages, images, entities, comparisons)")
            print("plus a master 'All Knowledge.base'. Idempotent; prior files are")
            print("snapshotted to .kb-trash/ before overwrite.")
            return 0

    bases_dir = os.path.join(root, "wiki", "bases")
    os.makedirs(bases_dir, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")

    active: List[Tuple[str, str, List[str]]] = []  # (name, folder_rel, order)

    for type_slug, title, order in _COLLECTIONS:
        folder = os.path.join(root, "wiki", "format", type_slug)
        folder_rel = f"wiki/format/{type_slug}"
        count = _count_pages(folder)
        if count == 0:
            print(f"  skipped {title} (empty collection)")
            continue
        active.append((title, folder_rel, order))
        content = _per_collection_base(title, folder_rel, order)
        path = os.path.join(bases_dir, f"{title}.base")
        changed = _write(path, content, root, stamp)
        status = "wrote" if changed else "unchanged"
        print(f"  {status} wiki/bases/{title}.base ({count} pages)")

    # Master base — one view per non-empty collection.
    master_content = _master_base(active)
    master_path = os.path.join(bases_dir, "All Knowledge.base")
    changed = _write(master_path, master_content, root, stamp)
    status = "wrote" if changed else "unchanged"
    print(f"  {status} wiki/bases/All Knowledge.base ({len(active)} views)")

    return 0
