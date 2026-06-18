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


_TWIMG_MD_IMG_RE = re.compile(
    r'!\[([^\]]*)\]\((https?://pbs\.twimg\.com/[^)]+?)\)',
    re.IGNORECASE,
)
_TWIMG_HTML_IMG_RE = re.compile(
    r'<img\b[^>]*\bsrc=["\'](https?://pbs\.twimg\.com/[^"\']+)["\'][^>]*>',
    re.IGNORECASE,
)
_TWIMG_NAME_PARAM_RE = re.compile(
    r'([?&]name=)(large|4096x4096|orig)\b', re.IGNORECASE
)


def rewrite_body(body: str) -> tuple[str, int]:
    """Convert pbs.twimg.com image refs to HTML <img width="600"> with
    a name=medium URL. Returns (new_body, n_changed).

    Why HTML not markdown: Obsidian honors `![alt|600](url)` width syntax
    only in Reading View. Edit Mode renders external image markdown at
    native pixel size with no constraint. HTML <img width="..."> works in
    both modes. See bin/lib/process_clip.py's _rewrite_twimg_images for
    the canonical rationale.
    """
    changes = [0]

    def _md_sub(m):
        alt, url = m.group(1), m.group(2)
        new_url = _TWIMG_NAME_PARAM_RE.sub(r'\1medium', url)
        # Strip any `|<width>` leftover from the old markdown-width form
        # (briefly used in 1.0.12 before we switched to HTML img). The
        # `|600` doesn't belong in HTML alt and reads like noise.
        alt_clean = re.sub(r'\|\d+\s*$', '', alt).strip() or 'Image'
        alt_attr = alt_clean.replace('"', '&quot;')
        changes[0] += 1
        return f'<img src="{new_url}" alt="{alt_attr}" width="600">'

    def _html_sub(m):
        url = m.group(1)
        new_url = _TWIMG_NAME_PARAM_RE.sub(r'\1medium', url)
        alt_match = re.search(r'\balt=["\']([^"\']*)["\']', m.group(0), re.IGNORECASE)
        alt_raw = alt_match.group(1) if alt_match else 'Image'
        # Strip stale `|<width>` from alt (legacy 1.0.12-form leftovers
        # that survived the initial markdown→HTML conversion with the
        # `|600` still glued onto the alt text).
        alt_clean = re.sub(r'\|\d+\s*$', '', alt_raw).strip() or 'Image'
        alt_attr = alt_clean.replace('"', '&quot;')
        # Already a canonical width=600 + name=medium + clean alt? Skip.
        original = m.group(0)
        canonical = f'<img src="{new_url}" alt="{alt_attr}" width="600">'
        if original == canonical:
            return original
        changes[0] += 1
        return canonical

    body = _TWIMG_MD_IMG_RE.sub(_md_sub, body)
    body = _TWIMG_HTML_IMG_RE.sub(_html_sub, body)
    return body, changes[0]


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
