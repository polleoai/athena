"""Detect two-or-more wiki pages pointing at the same `raw_path`.

A slug-collision bug class: if `bin/lib/slug.derive_slug` returns the same
slug for two distinct source URLs, the second capture overwrites the
first raw, and every prior wiki page that referenced the first raw now
silently points at unrelated content. Detection is one walk over the
wiki tree + a defaultdict.

Witnessed 2026-05-31: every `youtube.com/watch?v=…` URL produced the same
slug `youtube-com-watch` (urlparse() drops the query string), so three
distinct YouTube wiki pages all listed `raw_path: raw/videos/artifacts/
youtube-com-watch.md`. Slug fix lives in slug.py; this module is the
permanent guard for any future URL family that loses its uniqueness.

Used by `bin/kb lint` §30d and by the regression test in tests/.
Pure I/O — no side effects beyond reading the wiki tree.
"""

from __future__ import annotations

import glob
import os
import re
from collections import defaultdict
from pathlib import Path

_RAW_PATH_RE = re.compile(r'^raw_path:\s*"?([^"\n]+?)"?\s*$', re.MULTILINE)
_SKIP_NAMES = frozenset({"_TEMPLATE.md", "_Contents.md"})


def find_raw_path_collisions(vault_root: str | Path) -> dict[str, list[str]]:
    """Walk `<vault_root>/wiki/**/*.md` and group wiki pages by `raw_path`.

    Returns a dict keyed by raw_path → list of wiki page paths (relative
    to vault_root) that point at it. Only entries with len(list) >= 2
    (actual collisions) are included; non-colliding pages are omitted.

    Pages without a `raw_path:` field (synthesis pages, redirect stubs)
    are silently skipped — they're not part of this bug class.
    """
    vault = str(vault_root)
    buckets: dict[str, list[str]] = defaultdict(list)
    for f in glob.glob(os.path.join(vault, "wiki", "**", "*.md"), recursive=True):
        name = os.path.basename(f)
        if name in _SKIP_NAMES:
            continue
        try:
            head = open(f, "r", encoding="utf-8", errors="replace").read(2000)
        except OSError:
            continue
        m = _RAW_PATH_RE.search(head)
        if not m:
            continue
        buckets[m.group(1).strip()].append(os.path.relpath(f, vault))
    return {rp: wikis for rp, wikis in buckets.items() if len(wikis) >= 2}


# ─── Self-test ───────────────────────────────────────────────────────


if __name__ == "__main__":
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        v = Path(td)
        (v / "wiki" / "format" / "videos").mkdir(parents=True)
        for name, rp in [
            ("A", "raw/videos/artifacts/youtube-com-watch.md"),
            ("B", "raw/videos/artifacts/youtube-com-watch.md"),  # collides
            ("C", "raw/videos/artifacts/video-XYZ.md"),
        ]:
            (v / "wiki" / "format" / "videos" / f"{name}.md").write_text(
                f'---\ntitle: "{name}"\nraw_path: "{rp}"\n---\n'
            )
        out = find_raw_path_collisions(v)
        assert len(out) == 1, out
        assert out["raw/videos/artifacts/youtube-com-watch.md"] == sorted([
            "wiki/format/videos/A.md", "wiki/format/videos/B.md",
        ]) or sorted(out["raw/videos/artifacts/youtube-com-watch.md"]) == sorted([
            "wiki/format/videos/A.md", "wiki/format/videos/B.md",
        ])
    print("raw_path_collisions self-test passed.")
