#!/usr/bin/env python3
"""rewrite-twimg-sizes.py — one-time pass over existing raw files.

Rewrites every `pbs.twimg.com/...?name=large` (or `name=4096x4096` /
`name=orig`) image reference to `name=medium`, and ensures the alt text
carries Obsidian's `|600` width constraint. Same logic as the in-line
fix that landed in process_clip.py for new captures.

Why this exists: 1.0.x captured X.com images at `name=large` for ~6
months before the size fix. Existing raws still render oversized in
Obsidian (the image takes over the viewport and hides body text). This
script applies the same rewrite retroactively. Idempotent — re-runs are
no-ops.

Usage:
    python3 scripts/rewrite-twimg-sizes.py            # write changes
    python3 scripts/rewrite-twimg-sizes.py --dry-run  # report only

Safety:
- The rewrite is reversible (regex is unambiguous — `name=medium` →
  `name=large` would round-trip cleanly).
- The fix is purely display: the URL still resolves to a valid Twitter
  image, just at a different size.
- Files are rewritten via tmp + os.replace (atomic — never leaves a
  half-written file).
- Reports which files changed and how many image refs in each.
"""

import argparse
import os
import re
import sys
from pathlib import Path


_TWIMG_IMG_RE = re.compile(
    r'(!\[)([^\]]*)(\]\()(https?://pbs\.twimg\.com/[^)]+?)(\))',
    re.IGNORECASE,
)
_TWIMG_NAME_PARAM_RE = re.compile(
    r'([?&]name=)(large|4096x4096|orig)\b', re.IGNORECASE
)


def rewrite_body(body: str) -> tuple[str, int]:
    """Return (new_body, n_changed) — count of image refs that were modified."""
    changes = [0]

    def _sub(m):
        bang, alt, mid, url, close = m.groups()
        new_url = _TWIMG_NAME_PARAM_RE.sub(r'\1medium', url)
        if re.search(r'\|\d+$', alt):
            new_alt = alt
        else:
            new_alt = (alt.rstrip() + '|600') if alt.strip() else 'Image|600'
        if new_url == url and new_alt == alt:
            return m.group(0)
        changes[0] += 1
        return f'{bang}{new_alt}{mid}{new_url}{close}'

    new_body = _TWIMG_IMG_RE.sub(_sub, body)
    return new_body, changes[0]


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--vault', default='.', help='Vault root (default: cwd)')
    ap.add_argument('--dry-run', action='store_true', help='Report only, no writes')
    args = ap.parse_args()

    vault = Path(args.vault).resolve()
    raw_dir = vault / 'raw'
    if not raw_dir.is_dir():
        sys.exit(f'No raw/ directory in {vault}; run from the vault root.')

    total_files = 0
    total_changed_files = 0
    total_refs = 0
    for path in sorted(raw_dir.rglob('*.md')):
        # Skip atomic-write artifacts and trash bundles.
        if path.name.endswith('.tmp') or '.kb-trash' in str(path):
            continue
        total_files += 1
        try:
            body = path.read_text(encoding='utf-8')
        except (OSError, UnicodeDecodeError):
            continue
        new_body, n = rewrite_body(body)
        if n == 0:
            continue
        total_changed_files += 1
        total_refs += n
        rel = path.relative_to(vault)
        marker = '[dry-run]' if args.dry_run else 'rewrote'
        print(f'  {marker} {rel}  ({n} image ref{"s" if n != 1 else ""})')
        if not args.dry_run:
            tmp = path.with_suffix(path.suffix + '.tmp')
            tmp.write_text(new_body, encoding='utf-8')
            os.replace(tmp, path)

    verb = 'Would rewrite' if args.dry_run else 'Rewrote'
    print(f'\n{verb} {total_refs} twimg image ref(s) across '
          f'{total_changed_files}/{total_files} raw file(s).')


if __name__ == '__main__':
    main()
