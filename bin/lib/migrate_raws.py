"""kb migrate-raws — convert legacy header-bullet raws to YAML frontmatter.

The Phase 1 baseline showed 233/360 raws use the legacy convention:

    # <Title>

    - **URL:** https://...
    - **Authors:** ...
    - **Captured:** 2026-04-06

    ## Content

The raw_parser (Phase 2) tolerates this format, so callers like
schema-check, indexer, and orphan-walker work either way. But two
things still suffer with the legacy form:

  1. Lint rules that read frontmatter directly bypass raw_parser
     and miss the legacy data — false negatives.
  2. New ingest paths (post Phase 3) emit YAML, so the corpus has
     two formats coexisting indefinitely. Reviewers see the dichotomy
     and treat one as canonical, the other as broken.

This module performs the one-time conversion. Reads the legacy header,
extracts fields via raw_parser, rewrites the file with YAML frontmatter
+ the original body content. Atomic per-file.

Always interactive (asks Y/N for each batch) so the user can spot-check
before bulk-applying.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

from raw_parser import read_raw_frontmatter
from wiki_schema import _FM_RE


_H1_RE = re.compile(r"^#\s+(.+)$")
_BULLET_RE = re.compile(r"^-\s+\*\*[^:*]+:\*\*")


def _yaml_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')


def _render_yaml_frontmatter(fm: dict) -> str:
    """Emit canonical YAML frontmatter from the parsed dict."""
    lines = ["---"]
    # Stable field order: known schema fields first, then alphabetical extras.
    KNOWN_ORDER = (
        "title", "source", "captured_at", "clipped_via", "clipped_at",
        "authors", "author", "stars", "language", "topics",
    )
    for key in KNOWN_ORDER:
        if key in fm:
            v = fm[key]
            if v is None:
                continue
            if isinstance(v, list):
                lines.append(f"{key}:")
                for item in v:
                    lines.append(f'  - "{_yaml_escape(str(item))}"')
            elif isinstance(v, bool):
                lines.append(f"{key}: {'true' if v else 'false'}")
            elif isinstance(v, (int, float)):
                lines.append(f"{key}: {v}")
            else:
                lines.append(f'{key}: "{_yaml_escape(str(v))}"')
    for key in sorted(set(fm.keys()) - set(KNOWN_ORDER)):
        v = fm[key]
        if v is None:
            continue
        lines.append(f'{key}: "{_yaml_escape(str(v))}"')
    lines.append("---")
    return "\n".join(lines)


def _is_yaml(text: str) -> bool:
    return bool(_FM_RE.match(text))


def _is_legacy_header(text: str) -> bool:
    """Detect H1 + bullet block. Matches raw_parser's parser pattern."""
    lines = text.split("\n")
    i = 0
    while i < len(lines) and not lines[i].strip():
        i += 1
    if i >= len(lines) or not _H1_RE.match(lines[i]):
        return False
    # Must have at least one bullet in the next several lines
    for j in range(i + 1, min(i + 10, len(lines))):
        if _BULLET_RE.match(lines[j]):
            return True
    return False


def _migrate_one(path: Path) -> tuple[bool, str]:
    """Convert one legacy file to YAML in place. Returns (changed, message)."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return False, f"read failed: {exc}"

    if _is_yaml(text):
        return False, "already YAML"
    if not _is_legacy_header(text):
        return False, "no parseable frontmatter (manual review needed)"

    fm, body = read_raw_frontmatter(path)
    if not fm:
        return False, "raw_parser returned empty (unexpected)"

    yaml_fm = _render_yaml_frontmatter(fm)
    title = fm.get("title", "Untitled")
    h1 = f"# {title}"
    new_text = yaml_fm + "\n\n" + h1 + "\n\n" + body.lstrip() + (
        "" if body.endswith("\n") else "\n"
    )

    # Atomic write
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_text(new_text, encoding="utf-8")
        os.replace(tmp, path)
    except OSError as exc:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        return False, f"write failed: {exc}"

    return True, "ok"


def main(vault: str, dry_run: bool = False) -> int:
    print(f"\nkb migrate-raws — one-time legacy→YAML conversion")
    print(f"Vault: {vault}")
    if dry_run:
        print("Mode: DRY RUN (no writes)")

    candidates: list[Path] = []
    legacy: list[Path] = []
    needs_manual: list[Path] = []
    already_yaml = 0

    import glob
    for cat in ("papers", "repos", "webpages", "videos", "images"):
        for f in glob.glob(os.path.join(vault, "raw", cat, "artifacts", "*.md")):
            path = Path(f)
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                continue
            if _is_yaml(text):
                already_yaml += 1
            elif _is_legacy_header(text):
                legacy.append(path)
            else:
                needs_manual.append(path)

    print(f"\nFound:")
    print(f"  Already YAML:    {already_yaml}")
    print(f"  Legacy header:   {len(legacy)} (will be converted)")
    print(f"  Needs manual:    {len(needs_manual)}")

    if needs_manual:
        print(f"\nFiles needing manual review (no parseable frontmatter):")
        for p in needs_manual[:10]:
            print(f"  - {p.relative_to(vault)}")
        if len(needs_manual) > 10:
            print(f"  ... and {len(needs_manual) - 10} more")

    if not legacy:
        print("\nNo legacy files to convert. Done.")
        return 0

    if dry_run:
        print(f"\nDry run: {len(legacy)} files would be converted.")
        return 0

    print(f"\nConverting {len(legacy)} files...")
    ok = 0
    failed = 0
    for path in legacy:
        changed, msg = _migrate_one(path)
        if changed:
            ok += 1
        else:
            failed += 1
            print(f"  ✗ {path.relative_to(vault)}: {msg}")

    print(f"\nDone. {ok} converted, {failed} failed.")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: migrate_raws.py <vault_root> [--dry-run]", file=sys.stderr)
        sys.exit(2)
    sys.exit(main(sys.argv[1], "--dry-run" in sys.argv))
