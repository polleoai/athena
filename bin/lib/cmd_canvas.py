"""`kb canvas <topic>` — render a topic page's cross-reference graph as a 1-hop
radial Obsidian Canvas (JSON Canvas) at `wiki/canvas/<topic>.canvas`.

The center node is the topic page; its frontmatter `related:` neighbors are placed
evenly on a ring around it, one edge from center to each neighbor. Nodes are colored
by page type (Obsidian canvas color slots "1".."6"). Output is deterministic and
idempotent: a rerun with no vault change is byte-identical, and any prior `.canvas`
is snapshotted to `.kb-trash/<timestamp>_canvas/` before overwrite so nothing is lost.

A `--depth 2` extension is reserved for the future (per the kb-views design spec);
this command renders 1 hop only.
"""
from __future__ import annotations

import glob
import json
import math
import os
import re
import time
from typing import Dict, List, Optional, Tuple

# Ring geometry (integer coordinates so reruns are byte-identical).
_RADIUS = 520
_CENTER_W, _CENTER_H = 360, 100
_NEIGHBOR_W, _NEIGHBOR_H = 300, 80

# Page-type color keys (Obsidian canvas color slots). Resolved off the wiki path.
# (path-substring, color). First match wins; the topic center is always "5".
_COLOR_BY_PATH: List[Tuple[str, str]] = [
    ("wiki/topics", "5"),
    ("wiki/format/papers", "4"),
    ("wiki/format/repos", "2"),
    ("wiki/insights", "1"),
    ("wiki/format/videos", "6"),
    ("wiki/format/entities", "3"),
    ("wiki/format/webpages", "4"),
]


def _index_wiki_pages(root: str) -> Dict[str, str]:
    """Map wiki page basename (no `.md`) -> vault-relative path with forward
    slashes. Mirrors the lint body's `wiki_pages` resolution dict — the
    established wikilink-resolution pattern in this codebase."""
    pages: Dict[str, str] = {}
    pattern = os.path.join(root, "wiki", "**", "*.md")
    for abs_path in sorted(glob.glob(pattern, recursive=True)):
        base = os.path.basename(abs_path)
        if base in (".gitkeep", "_TEMPLATE.md") or base.startswith("_"):
            continue
        name = os.path.splitext(base)[0]
        rel = os.path.relpath(abs_path, root).replace(os.sep, "/")
        # First glob hit wins (sorted) so resolution is deterministic on
        # the rare duplicate-basename case.
        pages.setdefault(name, rel)
    return pages


def _read_related(topic_path: str) -> List[str]:
    """Parse the topic page frontmatter `related:` block into a list of
    `[[Page Name]]` target names (the inner text). Handles the canonical
    block-list form (`- "[[Name]]"`) emitted by Athena's writers."""
    try:
        with open(topic_path, "r", encoding="utf-8") as fh:
            content = fh.read()
    except (IOError, UnicodeDecodeError):
        return []
    m = re.match(r"^---\s*\n(.*?)\n---", content, re.DOTALL)
    if not m:
        return []
    fm = m.group(1).split("\n")
    names: List[str] = []
    in_related = False
    for line in fm:
        if re.match(r"^related\s*:", line):
            in_related = True
            # Inline form: related: ["[[A]]", "[[B]]"]
            inline = line.split(":", 1)[1].strip()
            if inline.startswith("[") and inline.endswith("]"):
                for chunk in inline[1:-1].split(","):
                    lm = re.search(r"\[\[([^\]|]+?)(?:\|[^\]]+?)?\]\]", chunk)
                    if lm:
                        names.append(lm.group(1).strip())
                in_related = False
            continue
        if in_related:
            # Block list items: `  - "[[Name]]"`.
            if re.match(r"^\s+-\s+", line):
                lm = re.search(r"\[\[([^\]|]+?)(?:\|[^\]]+?)?\]\]", line)
                if lm:
                    names.append(lm.group(1).strip())
            elif line.strip() == "":
                continue
            else:
                # A non-list, non-blank line ends the related: block.
                in_related = False
    return names


def _resolve_topic(topic: str, root: str, pages: Dict[str, str]) -> Optional[str]:
    """Resolve a user-supplied topic to a vault-relative wiki path.

    Tries, in order: exact file under wiki/topics/, case-insensitive match in
    wiki/topics/, then any wiki page whose basename matches (case-insensitively)
    so a fully-qualified title also resolves."""
    topics_dir = os.path.join(root, "wiki", "topics")
    direct = os.path.join(topics_dir, f"{topic}.md")
    if os.path.isfile(direct):
        return os.path.relpath(direct, root).replace(os.sep, "/")

    if os.path.isdir(topics_dir):
        for name in sorted(os.listdir(topics_dir)):
            if not name.endswith(".md") or name.startswith("_"):
                continue
            if os.path.splitext(name)[0].lower() == topic.lower():
                return f"wiki/topics/{name}"

    # Fall back to any wiki page (e.g. an LLM-authored full title).
    for name, rel in pages.items():
        if name.lower() == topic.lower():
            return rel
    return None


def _color_for(rel_path: str) -> Optional[str]:
    """Color key for a vault-relative wiki path, or None when no type matches."""
    for substr, color in _COLOR_BY_PATH:
        if rel_path.startswith(substr):
            return color
    return None


def _sides_for_angle(angle: float) -> Tuple[str, str]:
    """Pick a (fromSide, toSide) pair from the neighbor's ring angle so the edge
    leaves the center toward the neighbor. Deterministic per angle quadrant."""
    deg = math.degrees(angle) % 360
    if deg < 45 or deg >= 315:
        return "right", "left"
    if deg < 135:
        return "bottom", "top"
    if deg < 225:
        return "left", "right"
    return "top", "bottom"


def _build_canvas(topic_rel: str, neighbors: List[str]) -> Dict:
    """Assemble the JSON Canvas dict. Neighbors are pre-sorted by resolved path
    so node order, ids, and coordinates are stable across runs."""
    nodes: List[Dict] = [{
        "id": "center",
        "type": "file",
        "file": topic_rel,
        "x": 0,
        "y": 0,
        "width": _CENTER_W,
        "height": _CENTER_H,
        "color": "5",  # topic center
    }]
    edges: List[Dict] = []
    n = len(neighbors)
    for i, rel in enumerate(neighbors):
        angle = 2 * math.pi * i / n if n else 0.0
        x = int(round(_RADIUS * math.cos(angle)))
        y = int(round(_RADIUS * math.sin(angle)))
        node: Dict = {
            "id": f"n{i}",
            "type": "file",
            "file": rel,
            "x": x,
            "y": y,
            "width": _NEIGHBOR_W,
            "height": _NEIGHBOR_H,
        }
        color = _color_for(rel)
        if color is not None:
            node["color"] = color
        nodes.append(node)

        from_side, to_side = _sides_for_angle(angle)
        edges.append({
            "id": f"e{i}",
            "fromNode": "center",
            "toNode": f"n{i}",
            "fromSide": from_side,
            "toSide": to_side,
        })
    return {"nodes": nodes, "edges": edges}


def _snapshot_if_changed(path: str, new_content: str, root: str, stamp: str) -> None:
    """Snapshot a prior, differing `.canvas` to `.kb-trash/<stamp>_canvas/` before
    overwrite. No-op when the file is absent or already byte-identical."""
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as fh:
        if fh.read() == new_content:
            return
    trash_dir = os.path.join(root, ".kb-trash", f"{stamp}_canvas")
    os.makedirs(trash_dir, exist_ok=True)
    with open(path, "r", encoding="utf-8") as src:
        prior = src.read()
    with open(os.path.join(trash_dir, os.path.basename(path)), "w", encoding="utf-8") as dst:
        dst.write(prior)


def handle(argv: List[str], root: str) -> int:
    topic_args: List[str] = []
    for arg in argv:
        if arg in ("--help", "-h"):
            print("Usage: kb canvas <topic>")
            print("")
            print("Render a topic page's related: cross-reference graph as a")
            print("1-hop radial Obsidian Canvas at wiki/canvas/<topic>.canvas.")
            print("Idempotent; prior files are snapshotted to .kb-trash/ first.")
            return 0
        topic_args.append(arg)

    if not topic_args:
        print("Usage: kb canvas <topic>")
        return 1
    topic = " ".join(topic_args).strip()

    pages = _index_wiki_pages(root)
    topic_rel = _resolve_topic(topic, root, pages)
    if topic_rel is None:
        print(f"No topic page found for '{topic}' — try `kb list --topics`")
        return 1

    topic_abs = os.path.join(root, topic_rel)
    related_names = _read_related(topic_abs)

    resolved: List[str] = []
    for name in related_names:
        rel = pages.get(name)
        if rel is None:
            print(f"  skipped unresolved link: [[{name}]]")
            continue
        if rel == topic_rel:
            continue  # never link a page to itself
        resolved.append(rel)

    # Deterministic order: sort by resolved path, de-duplicate.
    neighbors = sorted(dict.fromkeys(resolved))

    canvas = _build_canvas(topic_rel, neighbors)
    content = json.dumps(canvas, ensure_ascii=False, indent="\t") + "\n"

    canvas_dir = os.path.join(root, "wiki", "canvas")
    os.makedirs(canvas_dir, exist_ok=True)
    # The on-disk filename mirrors the topic page's own filename stem so the
    # canvas sits next to a predictable name (and reruns target the same file).
    stem = os.path.splitext(os.path.basename(topic_rel))[0]
    out_path = os.path.join(canvas_dir, f"{stem}.canvas")

    if os.path.exists(out_path):
        with open(out_path, "r", encoding="utf-8") as fh:
            if fh.read() == content:
                print(f"  unchanged wiki/canvas/{stem}.canvas "
                      f"(center + {len(neighbors)} neighbors)")
                return 0

    stamp = time.strftime("%Y%m%d-%H%M%S")
    _snapshot_if_changed(out_path, content, root, stamp)
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(content)
    print(f"  wrote wiki/canvas/{stem}.canvas (center + {len(neighbors)} neighbors)")
    return 0
