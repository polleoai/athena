"""kb schema-check — validate vault frontmatter against schemas.py contracts.

Read-only diagnostic. Walks raw + wiki, parses each file's frontmatter,
attempts to validate against the appropriate Pydantic model, and reports
violations grouped by error class with sample paths.

This is the Phase 1 deliverable of the schema refactor. The numbers it
produces tell us how aggressive Phase 2/3 strict enforcement can be
without invalidating existing data — and which schema fields need
relaxation vs. which existing data needs cleanup.

Always exits 0. Never modifies anything.
"""

from __future__ import annotations

import glob
import os
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Iterable

# Local-package imports (kb dispatch sets sys.path)
from raw_parser import read_raw_frontmatter
from schemas import (
    RawFrontmatter,
    WikiShape,
    detect_wiki_shape,
    model_for_shape,
)
from url_canonical import canonicalize
from wiki_schema import read_frontmatter

from pydantic import ValidationError


def _short_error(exc: ValidationError) -> Iterable[str]:
    """Pydantic errors can be a wall of JSON. Surface (loc, msg) per error
    so we can group across files."""
    for err in exc.errors():
        loc = ".".join(str(x) for x in err["loc"])
        msg = err["msg"]
        # Trim long messages — keep the rule, drop the value echo
        msg = msg.split("[", 1)[0].strip()
        yield f"{loc}: {msg}"


def _walk_raw(vault: str) -> Iterable[Path]:
    for cat in ("papers", "repos", "webpages", "videos", "images"):
        for f in glob.glob(os.path.join(vault, "raw", cat, "artifacts", "*.md")):
            yield Path(f)


def _walk_wiki(vault: str) -> Iterable[Path]:
    for f in glob.glob(os.path.join(vault, "wiki", "format", "**", "*.md"),
                       recursive=True):
        if os.path.basename(f) in ("_Contents.md", "_TEMPLATE.md"):
            continue
        yield Path(f)


def _check_raw(vault: str) -> tuple[int, dict[str, list[str]]]:
    """Returns (total_checked, {error_class: [sample_paths]})."""
    errors: dict[str, list[str]] = defaultdict(list)
    total = 0
    for path in _walk_raw(vault):
        total += 1
        # Tolerate both YAML and legacy header-bullet form (see raw_parser).
        fm, _body = read_raw_frontmatter(path)
        if not fm:
            errors["raw: missing frontmatter (neither YAML nor legacy header)"].append(
                str(path.relative_to(vault))
            )
            continue
        try:
            RawFrontmatter(**fm)
        except ValidationError as exc:
            for short in _short_error(exc):
                key = f"raw: {short}"
                if len(errors[key]) < 5:
                    errors[key].append(str(path.relative_to(vault)))
    return total, errors


def _check_wiki(vault: str) -> tuple[int, dict[str, list[str]]]:
    errors: dict[str, list[str]] = defaultdict(list)
    total = 0
    shape_counts: dict[str, int] = defaultdict(int)
    for path in _walk_wiki(vault):
        total += 1
        fm, _body = read_frontmatter(path)
        if not fm:
            errors["wiki: missing frontmatter"].append(str(path.relative_to(vault)))
            continue
        shape = detect_wiki_shape(fm)
        shape_counts[shape.value] += 1
        Model = model_for_shape(shape)
        try:
            Model(**fm)
        except ValidationError as exc:
            for short in _short_error(exc):
                key = f"wiki[{shape.value}]: {short}"
                if len(errors[key]) < 5:
                    errors[key].append(str(path.relative_to(vault)))
    # Surface shape distribution as a "non-error" grouped report at the end
    errors["__shape_counts__"] = [
        f"{shape}: {n}" for shape, n in sorted(shape_counts.items())
    ]
    return total, errors


def _check_url_canonicalization(vault: str) -> tuple[int, list[str]]:
    """Find wiki pages whose `url:` field is not in canonical form.

    This is the easiest-to-fix class — Phase 3 url_writer.canonicalize
    will produce conformant URLs. Today: surfaces the count of pages
    that need touch-up."""
    non_canonical: list[str] = []
    total = 0
    for path in _walk_wiki(vault):
        fm, _ = read_frontmatter(path)
        url = fm.get("url")
        if not url or isinstance(url, list):
            continue
        total += 1
        canon = canonicalize(url).url
        if canon != url:
            if len(non_canonical) < 10:
                non_canonical.append(
                    f"{path.relative_to(vault)}\n      "
                    f"have: {url}\n      want: {canon}"
                )
    return total, non_canonical


def _print_report(label: str, total: int, errors: dict[str, list[str]]) -> int:
    """Print one report block. Returns count of distinct error classes."""
    shape_counts = errors.pop("__shape_counts__", None)
    print(f"\n{'=' * 60}")
    print(f"  {label}: {total} files")
    if shape_counts:
        print(f"  Shape distribution: {', '.join(shape_counts)}")
    print('=' * 60)
    if not errors:
        print("  No schema violations.")
        return 0
    # Sort error classes by frequency (most common first). Each entry's
    # sample list length is a lower bound on its true frequency (we cap
    # collection at 5 per class).
    by_freq = sorted(errors.items(), key=lambda kv: -len(kv[1]))
    for err_class, samples in by_freq:
        print(f"\n  [{len(samples):3d}+ files] {err_class}")
        for s in samples[:3]:
            print(f"    - {s}")
        if len(samples) > 3:
            print(f"    ... and {len(samples) - 3} more")
    return len(errors)


def _fix_url_field_in_file(path: Path, field_name: str) -> tuple[bool, str, str]:
    """Canonicalize one URL-bearing frontmatter field in `path`.

    field_name is 'url' (wiki) or 'source' (raw). Touches ONLY that
    field's value, never anything else. Returns (changed, original, canonical).

    Tolerates leaked-frontmatter-in-body (lint #48 class): if the field
    isn't in the leading FM block, scans the rest of the file for a
    similarly-shaped line (this catches the YouTube videos where the
    wiki frontmatter got pasted into the raw body).
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return False, "", ""
    field_re = re.compile(
        rf'^({field_name}:\s*)"?(https?://[^\s"\n]+?)"?\s*$', re.MULTILINE
    )
    # Try leading FM block first, then full text (covers leaked FM in body).
    leading = re.match(r"^(---\n)(.*?)(\n---\n)", text, re.DOTALL)
    if leading:
        m_leading = field_re.search(leading.group(2))
        if m_leading:
            target_block = leading.group(2)
            target_offset = leading.start(2)
        else:
            target_block = None
    else:
        target_block = None

    if target_block is None:
        # Fall back to whole-file scan
        m_anywhere = field_re.search(text)
        if not m_anywhere:
            return False, "", ""
        original = m_anywhere.group(2).strip()
        canon = canonicalize(original).url
        if canon == original:
            return False, original, canon
        new_text = text[:m_anywhere.start()] + f'{field_name}: "{canon}"' + text[m_anywhere.end():]
    else:
        m = field_re.search(target_block)
        original = m.group(2).strip()
        canon = canonicalize(original).url
        if canon == original:
            return False, original, canon
        new_block = target_block[:m.start()] + f'{field_name}: "{canon}"' + target_block[m.end():]
        new_text = text[:target_offset] + new_block + text[target_offset + len(target_block):]

    try:
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(new_text, encoding="utf-8")
        os.replace(tmp, path)
        return True, original, canon
    except OSError as exc:
        print(f"  ERROR writing {path}: {exc}", file=sys.stderr)
        return False, original, canon


def fix_urls(vault: str) -> int:
    """Auto-fix non-canonical URL fields across BOTH wiki and raw.

    Wiki pages have `url:` in frontmatter; raw artifacts have `source:`.
    Pre-0.9.2: this only touched wiki URLs, leaving raws non-canonical
    and creating wiki/raw mismatches (caught by the 'URL mismatch' lint).
    Now both are canonicalized in the same pass — idempotent, safe.
    """
    print(f"\nkb schema-check --fix-urls")
    print(f"Vault: {vault}\n")

    wiki_fixed: list[str] = []
    for path in _walk_wiki(vault):
        changed, orig, canon = _fix_url_field_in_file(path, "url")
        if changed:
            wiki_fixed.append(
                f"{path.relative_to(vault)}\n      {orig}\n   →  {canon}"
            )

    raw_fixed: list[str] = []
    for path in _walk_raw(vault):
        # Raws may use 'source:' (canonical writer) OR 'url:' (legacy).
        # Try both — first hit wins.
        for fname in ("source", "url"):
            changed, orig, canon = _fix_url_field_in_file(path, fname)
            if changed:
                raw_fixed.append(
                    f"{path.relative_to(vault)}\n      {orig}\n   →  {canon}"
                )
                break

    # MERGED-shape wiki pages have list-form `urls:\n  - "..."` blocks.
    # The scalar fix_url_field path above doesn't see them because it
    # anchors on `^url:`. Catch list entries here.
    list_fixed = 0
    for path in _walk_wiki(vault):
        n = _fix_url_list_in_file(path, "urls")
        list_fixed += n

    total = len(wiki_fixed) + len(raw_fixed) + list_fixed
    if not total:
        print("  All URLs already canonical.")
        return 0
    print(f"  Fixed {len(wiki_fixed)} wiki url(s) + {len(raw_fixed)} raw source(s):")
    for entry in (wiki_fixed + raw_fixed)[:20]:
        print(f"    - {entry}")
    if total > 20:
        print(f"    ... and {total - 20} more")
    if list_fixed:
        print(f"  (Plus {list_fixed} URL(s) inside list-form `urls:` blocks)")
    return 0


def _fix_url_list_in_file(path: Path, list_field: str) -> int:
    """Canonicalize URLs inside a list-form frontmatter block like

        urls:
          - "https://..."
          - "https://..."

    Returns count of URLs canonicalized. Only touches list items inside
    the leading FM block."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return 0
    leading = re.match(r"^(---\n)(.*?)(\n---\n)", text, re.DOTALL)
    if not leading:
        return 0
    fm_block = leading.group(2)
    list_block_re = re.compile(
        rf"^({list_field}:\n)((?:  -\s+\".*?\"\n)+)", re.MULTILINE
    )
    block_m = list_block_re.search(fm_block + "\n")
    if not block_m:
        return 0

    list_body = block_m.group(2)
    item_re = re.compile(r'^(  -\s+")([^"]+)(")', re.MULTILINE)
    fixed = 0
    new_list_body = list_body

    def _sub(m: re.Match) -> str:
        nonlocal fixed
        original = m.group(2)
        if not original.startswith(("http://", "https://")):
            return m.group(0)
        canon = canonicalize(original).url
        if canon == original:
            return m.group(0)
        fixed += 1
        return f"{m.group(1)}{canon}{m.group(3)}"

    new_list_body = item_re.sub(_sub, list_body)
    if not fixed:
        return 0

    new_fm = fm_block[:block_m.start()] + block_m.group(1) + new_list_body + fm_block[block_m.end() - 1:]
    new_text = leading.group(1) + new_fm + leading.group(3) + text[leading.end():]
    try:
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(new_text, encoding="utf-8")
        os.replace(tmp, path)
    except OSError as exc:
        print(f"  ERROR writing {path}: {exc}", file=sys.stderr)
        return 0
    return fixed


def fix_merged_singletons(vault: str) -> int:
    """Convert merged-shape wiki pages with only 1 raw_path to standard shape.

    Some pages were created with `raw_paths:` (list form) but only ever
    accumulated 1 entry. Schema demands ≥2 for MERGED. Auto-downgrade
    to STANDARD by collapsing `raw_paths:` → `raw_path:` and `urls:` →
    `url:` (or just removing if empty). Safe: same content, valid shape."""
    print(f"\nkb schema-check --fix-merged-singletons")
    print(f"Vault: {vault}\n")

    fixed: list[str] = []
    for path in _walk_wiki(vault):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        m = re.match(r"^(---\n)(.*?)(\n---\n)", text, re.DOTALL)
        if not m:
            continue
        fm_block = m.group(2)
        # Find raw_paths: list-form
        rp_m = re.search(
            r"^raw_paths:\n((?:  -\s+\".*?\"\n)+)",
            fm_block + "\n",
            re.MULTILINE,
        )
        if not rp_m:
            continue
        items = re.findall(r'  -\s+"(.*?)"', rp_m.group(1))
        if len(items) != 1:
            continue
        # Replace `raw_paths:\n  - "x"` → `raw_path: "x"`
        new_fm = (
            fm_block[:rp_m.start()]
            + f'raw_path: "{items[0]}"'
            + fm_block[rp_m.end() - 1:]  # -1 to drop trailing \n added above
        )
        # Also collapse urls: list with 1 entry → url: scalar (if present)
        u_m = re.search(
            r"^urls:\n((?:  -\s+\".*?\"\n)+)",
            new_fm + "\n",
            re.MULTILINE,
        )
        if u_m:
            u_items = re.findall(r'  -\s+"(.*?)"', u_m.group(1))
            if len(u_items) == 1:
                new_fm = (
                    new_fm[:u_m.start()]
                    + f'url: "{u_items[0]}"'
                    + new_fm[u_m.end() - 1:]
                )
        new_text = m.group(1) + new_fm + m.group(3) + text[m.end():]
        try:
            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_text(new_text, encoding="utf-8")
            os.replace(tmp, path)
            fixed.append(str(path.relative_to(vault)))
        except OSError as exc:
            print(f"  ERROR writing {path}: {exc}", file=sys.stderr)

    if not fixed:
        print("  No singleton-merged pages found.")
    else:
        print(f"  Converted {len(fixed)} singleton-merged pages to standard shape:")
        for entry in fixed:
            print(f"    - {entry}")
    return 0


def _check_collision_bait_stubs(vault: str) -> list[tuple[str, str]]:
    """Find redirect stubs whose filename matches a collision-bait pattern.

    Bug class (sakawa LinkedIn / brijpandeyji LinkedIn): the Web Clipper
    drops every LinkedIn post with the chrome-stripped title 'Post |
    LinkedIn'. The fallback wiki-page name builder produces 'LinkedIn:
    Post LinkedIn' for all of them. After the writer-side fix in
    wiki_page.py:_existing_page_matches_url, redirect stubs no longer
    intercept future captures — but their continued presence in the
    vault is a historical artifact worth surfacing so the user can decide
    to trash or rename them.

    This is a soft-warning section — no auto-fix. Renaming a stub has
    wikilink update implications that require user judgment."""
    bait_patterns = [
        re.compile(r"^LinkedIn:\s*Post\s+LinkedIn", re.IGNORECASE),
        re.compile(r"^LinkedIn:\s*Feed\s+LinkedIn", re.IGNORECASE),
        re.compile(r"^X:\s*Post(\s+X)?$", re.IGNORECASE),
        re.compile(r"^Web:\s*Untitled", re.IGNORECASE),
        re.compile(r"^Web:\s*Page$", re.IGNORECASE),
    ]
    found: list[tuple[str, str]] = []
    for path in _walk_wiki(vault):
        stem = path.stem
        if not any(p.search(stem) for p in bait_patterns):
            continue
        try:
            head = path.read_text(encoding="utf-8")[:512]
        except OSError:
            continue
        if not re.search(r"^redirect:\s*(true|\"true\")\s*$", head, re.MULTILINE):
            continue
        m = re.search(
            r'^redirect_to:\s*"?([^"\n]+?)"?\s*$', head, re.MULTILINE
        )
        target = m.group(1).strip() if m else "(unknown)"
        found.append((str(path.relative_to(vault)), target))
    return found


def main(vault: str) -> int:
    print(f"\nkb schema-check — Phase 1 diagnostic")
    print(f"Vault: {vault}")

    raw_total, raw_errors = _check_raw(vault)
    raw_classes = _print_report("RAW frontmatter", raw_total, raw_errors)

    wiki_total, wiki_errors = _check_wiki(vault)
    wiki_classes = _print_report("WIKI frontmatter", wiki_total, wiki_errors)

    url_total, url_non_canon = _check_url_canonicalization(vault)
    print(f"\n{'=' * 60}")
    print(f"  URL canonicalization: {url_total} wiki pages with `url:` field")
    print('=' * 60)
    if not url_non_canon:
        print("  All URLs already canonical.")
    else:
        print(f"\n  [{len(url_non_canon):3d}+ pages] non-canonical URL form")
        for s in url_non_canon[:5]:
            print(f"    - {s}")
        if len(url_non_canon) > 5:
            print(f"    ... and {len(url_non_canon) - 5} more")

    bait_stubs = _check_collision_bait_stubs(vault)
    print(f"\n{'=' * 60}")
    print(f"  Collision-bait redirect stubs (historical traps)")
    print('=' * 60)
    if not bait_stubs:
        print("  None found.")
    else:
        print(f"\n  [{len(bait_stubs):3d} stubs] consider trashing — no longer intercept future captures, but clutter listings")
        for stub_path, target in bait_stubs[:10]:
            print(f"    - {stub_path}\n        → {target}")
        if len(bait_stubs) > 10:
            print(f"    ... and {len(bait_stubs) - 10} more")

    print(f"\n{'═' * 60}")
    summary = (
        f"{raw_total} raw + {wiki_total} wiki files checked · "
        f"{raw_classes} raw + {wiki_classes} wiki error classes · "
        f"{len(url_non_canon)} URL canonicalization fixes pending · "
        f"{len(bait_stubs)} collision-bait stubs"
    )
    print(f"  {summary}")
    print(f"{'═' * 60}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: kb_schema_check.py <vault_root>", file=sys.stderr)
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
