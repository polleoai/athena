"""`kb remove <page>` -- soft-delete a wiki page (and optionally its raw) to
the .kb-trash/ ledger, clean up references, and update index counts.

Lifted verbatim from the `remove)` arm of bin/kb-legacy. The Bash wrapper
emitted a deprecation note to stderr UNLESS `--with-raw` was present (which
`kb delete` always forwards) -- reproduced here. The heredoc body's
sys.argv[1] (vault) becomes `root`, sys.argv[2:] (post-subcommand args) becomes
`argv`, and the top-level sys.exit(N) calls become `return N`.
"""
from __future__ import annotations

import glob
import os
import re
import sys
from typing import List


def handle(argv: List[str], root: str) -> int:
    # Notify users -- but only when invoked directly, not when `kb delete`
    # forwards here (we detect by looking for --with-raw which delete
    # always passes). Mirrors the Bash arm's `case " $* "` (stderr only).
    if "--with-raw" not in argv:
        sys.stderr.write(
            "Note: 'kb remove' is deprecated; use 'kb delete' instead "
            "(same effect, no orphan-raw resurrection).\n"
        )

    KB = root
    args = argv

    # ── Parse arguments ──────────────────────────────────
    keep_raw = True
    page_name = None
    confirmed = False
    cascade = False
    i = 0
    while i < len(args):
        if args[i] in ('--with-raw', '--delete-raw'):
            keep_raw = False; i += 1
        elif args[i] in ('--yes', '-y'):
            confirmed = True; i += 1
        elif args[i] == '--cascade':
            cascade = True; i += 1
        elif args[i] in ('--help', '-h'):
            print("""Usage: kb remove <page> [--with-raw] [--cascade] [--yes]

Delete a wiki page and clean up all references to it.

Arguments:
  page             Page to remove (exact filename without .md)
  --with-raw       Also delete the raw source file(s)
  --cascade        If page is a hub, also delete all child pages
  --yes, -y        Skip confirmation prompt

If the page is a hub (has child pages linked in its body):
  Default:    children stay, just lose their hub link (ungroup)
  --cascade:  hub AND all children are deleted

Examples:
  kb remove "Career Ops"                         # remove page, keep raw
  kb remove "Career Ops" --with-raw              # also delete raw source
  kb remove "Stanford AI Courses"                # remove hub, ungroup children
  kb remove "Stanford AI Courses" --cascade      # remove hub + all children
""")
            return 0
        else:
            if page_name is None:
                page_name = args[i]
            i += 1

    if not page_name:
        print("Error: need a page name")
        print('Usage: kb remove <page> [--with-raw] [--cascade] [--yes]')
        return 1

    # ── Find the page ────────────────────────────────────
    def find_wiki_page(name):
        for f in glob.glob(os.path.join(KB, 'wiki', '**', '*.md'), recursive=True):
            if os.path.splitext(os.path.basename(f))[0] == name:
                return f
        return None

    page_path = find_wiki_page(page_name)
    if not page_path:
        print(f"Error: page not found: {page_name}")
        return 1

    # ── Extract raw paths ────────────────────────────────
    raw_paths = []
    with open(page_path, 'r', encoding='utf-8') as f:
        content = f.read()
    m = re.match(r'^---\s*\n(.*?)\n---', content, re.DOTALL)
    if m:
        fm_text = m.group(1)
        # Single raw_path
        rm = re.search(r'^raw_path:\s*"?([^"\n]+)"?', fm_text, re.MULTILINE)
        if rm and rm.group(1).strip():
            raw_paths.append(rm.group(1).strip())
        # Multiple raw_paths
        in_list = False
        for line in fm_text.split('\n'):
            if re.match(r'^raw_paths:', line):
                in_list = True; continue
            if in_list:
                lm = re.match(r'^\s+-\s+"?([^"]+)"?', line)
                if lm:
                    raw_paths.append(lm.group(1).strip())
                elif line.strip() and not line.startswith(' '):
                    in_list = False

    # ── Find references ──────────────────────────────────
    ref_count = 0
    for f in glob.glob(os.path.join(KB, 'wiki', '**', '*.md'), recursive=True):
        if f == page_path:
            continue
        if os.path.basename(f) in ('.gitkeep', '_TEMPLATE.md'):
            continue
        with open(f, 'r') as fh:
            if f'[[{page_name}]]' in fh.read():
                ref_count += 1

    # ── Detect hub (page has [[child]] links in body table rows) ──
    children = []
    # A hub has wikilinks in table rows in its body (after frontmatter)
    body_start = content.find('---', content.find('---') + 3)
    if body_start > 0:
        body = content[body_start + 3:]
        # Find [[links]] inside table rows (lines starting with |)
        for line in body.split('\n'):
            if line.startswith('|') and '[[' in line:
                for cm in re.finditer(r'\[\[([^\]|]+?)(?:\|[^\]]+?)?\]\]', line):
                    child = cm.group(1)
                    if child != page_name:
                        children.append(child)

    is_hub = len(children) > 0

    # ── Show what will happen ────────────────────────────
    print(f"Page:       {os.path.relpath(page_path, KB)}")
    if raw_paths:
        for rp in raw_paths:
            print(f"Raw source: {rp}{'  (will delete)' if not keep_raw else '  (keeping)'}")
    print(f"References: {ref_count} other pages link to this")

    if is_hub:
        print()
        print(f"*** This is a hub page with {len(children)} children:")
        for c in children:
            print(f"    - {c}")
        if cascade:
            print()
            print(f"  --cascade: hub AND all {len(children)} children will be DELETED")
        else:
            print()
            print(f"  Children will be ungrouped (kept, but hub link removed)")
            print(f"  Use --cascade to delete children too")
    print()

    if not keep_raw:
        print("WARNING: --with-raw will permanently delete the raw source file(s)")
        print()

    # ── Confirm ──────────────────────────────────────────
    if not confirmed:
        try:
            answer = input("Proceed? (y/N) ").strip().lower()
            if answer != 'y':
                print("Cancelled.")
                return 0
        except (EOFError, KeyboardInterrupt):
            print("\nCancelled.")
            return 0

    # ── Soft delete: move to trash ───────────────────────
    sys.path.insert(0, os.path.join(KB, 'bin', 'lib'))
    from snapshot import trash_files

    # Collect all files to trash
    files_to_trash = [page_path]

    # Cascade: also trash children
    if is_hub and cascade:
        for child_name in children:
            child_path = find_wiki_page(child_name)
            if child_path:
                files_to_trash.append(child_path)
                # Collect child raw paths
                if not keep_raw:
                    with open(child_path, 'r') as f:
                        cc = f.read()
                    cm = re.match(r'^---\s*\n(.*?)\n---', cc, re.DOTALL)
                    if cm:
                        for line in cm.group(1).split('\n'):
                            rm = re.match(r'^raw_path:\s*"?([^"\n]+)"?', line)
                            if rm and rm.group(1).strip():
                                raw_paths.append(rm.group(1).strip())
                            lm = re.match(r'^\s+-\s+"?([^"]+)"?', line)
                            if lm and lm.group(1).startswith('raw/'):
                                raw_paths.append(lm.group(1).strip())

    # Trash raw sources if requested (validate paths stay within KB)
    if not keep_raw:
        for rp in raw_paths:
            full_rp = os.path.join(KB, rp)
            real_rp = os.path.realpath(full_rp)
            if not real_rp.startswith(os.path.realpath(KB) + os.sep):
                print(f"WARNING: raw_path escapes KB root, skipping: {rp}")
                continue
            if os.path.exists(full_rp):
                files_to_trash.append(full_rp)
            # Also companion PDFs
            pdf_path = re.sub(r'\.md$', '.pdf', full_rp)
            if os.path.exists(pdf_path):
                files_to_trash.append(pdf_path)

    # Move to trash FIRST, then record in ledger
    desc = f"kb remove {page_name}"
    if cascade:
        desc += f" --cascade ({len(children)} children)"
    batch = trash_files(KB, files_to_trash, operation="remove", description=desc)
    print(f"Trashed {len(files_to_trash)} file(s) → .kb-trash/")
    print(f"  Use 'kb undo' to restore")

    # Record URL as 'removed' in url-resolved.tsv (AFTER successful trash)
    url = ''
    if m:
        for line in m.group(1).split('\n'):
            um = re.match(r'^url:\s*"?([^"\n]+)"?', line)
            if um:
                url = um.group(1).strip()
                break
    if url:
        # Canonical resolved-URL index is inbox/url-resolved.tsv (every other
        # reader/writer uses it — kb_commands.py, wiki_page.py, process_clip.py,
        # canonical_source.py). Recording 'removed' anywhere else silently
        # breaks the recapture-from-scratch guarantee (Operating Principle #4).
        # makedirs guard: 'a' mode creates the file but NOT its parent dir, so a
        # missing inbox/ would otherwise crash AFTER the trash already succeeded.
        resolved_path = os.path.join(KB, 'inbox', 'url-resolved.tsv')
        os.makedirs(os.path.dirname(resolved_path), exist_ok=True)
        with open(resolved_path, 'a') as f:
            f.write(f"removed\t{page_name}\t{url}\t{url}\t\n")

    # ── Clean up references in other pages ───────────────
    # Build list of all names to clean (page + children if cascade)
    names_to_clean = [page_name]
    if is_hub and cascade:
        names_to_clean.extend(children)

    cleaned = 0
    for f in sorted(glob.glob(os.path.join(KB, 'wiki', '**', '*.md'), recursive=True)):
        if os.path.basename(f) in ('.gitkeep', '_TEMPLATE.md'):
            continue
        if not os.path.exists(f):
            continue
        with open(f, 'r', encoding='utf-8') as fh:
            content = fh.read()
        original = content

        for name in names_to_clean:
            # Remove from related: frontmatter
            content = content.replace(f'  - "[[{name}]]"\n', '')

            # Remove table rows containing [[name]]
            lines = content.split('\n')
            new_lines = [l for l in lines if f'[[{name}]]' not in l]
            content = '\n'.join(new_lines)

            # Remove inline [[wikilinks]] in body text (replace with plain text)
            content = content.replace(f'[[{name}]]', name)

        if content != original:
            with open(f, 'w', encoding='utf-8') as fh:
                fh.write(content)
            cleaned += 1

    if cleaned:
        print(f"Cleaned references in {cleaned} files")

    # ── Update index.md count ────────────────────────────
    index_path = os.path.join(KB, 'index.md')
    if os.path.exists(index_path):
        with open(index_path, 'r') as f:
            idx = f.read()
        actual_wiki = len([f for f in glob.glob(os.path.join(KB, 'wiki', '**', '*.md'), recursive=True)
                           if '.gitkeep' not in f and '_TEMPLATE' not in f])
        actual_raw = len([f for f in glob.glob(os.path.join(KB, 'raw', '**', '*.md'), recursive=True)
                          if '.gitkeep' not in f and '_TEMPLATE' not in f])
        idx = re.sub(r'>\s*\d+\s+sources?\s*·\s*\d+\s+wiki pages?',
                     f'> {actual_raw} sources · {actual_wiki} wiki pages', idx)
        with open(index_path, 'w') as f:
            f.write(idx)

    print()
    print("Done.")
    print("Run 'kb lint' to verify.")
    return 0
