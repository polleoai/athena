import os, sys, glob
from pathlib import Path
KB = sys.argv[1]; args = sys.argv[2:]
sys.path.insert(0, os.path.join(KB, 'bin', 'lib'))
from asset_download import download_assets, IMG_RE, rewrite_wiki_body_assets

target_slug = None
if '--page' in args:
    target_slug = args[args.index('--page') + 1]

raw_glob = os.path.join(KB, 'raw/webpages/artifacts/*.md')
candidates = sorted(glob.glob(raw_glob))
if target_slug:
    candidates = [p for p in candidates if Path(p).stem == target_slug]
    if not candidates:
        print(f"No raw page found for slug: {target_slug}"); sys.exit(1)

processed = skipped = touched = wiki_touched = wiki_replacements = 0
for raw_path in candidates:
    slug = Path(raw_path).stem
    text = Path(raw_path).read_text(encoding='utf-8')
    # Cheap pre-check: does the page reference any http(s) image at all?
    # Trigger processing if either (a) HTTP(S) images need download, or
    # (b) `data:` URI placeholder stubs are present (they get stripped by
    # download_assets, not downloaded). Lazy-load placeholders left in
    # markdown produce huge base64 noise lines — see asset_download.py.
    needs_work = [m for m in IMG_RE.finditer(text)
                  if m.group(2).startswith(('http://', 'https://', 'data:'))]
    if not needs_work:
        skipped += 1; continue
    remote_imgs = [m for m in needs_work if m.group(2).startswith(('http://', 'https://'))]
    data_imgs = [m for m in needs_work if m.group(2).startswith('data:')]
    msg_parts = []
    if remote_imgs: msg_parts.append(f"{len(remote_imgs)} remote")
    if data_imgs: msg_parts.append(f"{len(data_imgs)} data: stub(s)")
    print(f"  {slug} — {', '.join(msg_parts)}")
    new_text = download_assets(text, slug, Path(KB))
    processed += 1
    if new_text != text:
        Path(raw_path).write_text(new_text, encoding='utf-8'); touched += 1
    # Propagate the asset rewrite into the wiki page's body so it stops
    # 404-ing on the same hot-link URLs the raw just localized.
    n_wiki = rewrite_wiki_body_assets(slug, Path(KB))
    if n_wiki:
        wiki_touched += 1
        wiki_replacements += n_wiki
        print(f"    └─ wiki body: {n_wiki} ref(s) rewritten")

print()
print(f"Backfill complete: {processed} processed, {touched} raw rewritten, {skipped} already local.")
if wiki_touched:
    print(f"  Wiki: {wiki_touched} page(s) updated with {wiki_replacements} asset reference(s).")
print(f"Failures (if any) logged to inbox/asset-retry.tsv. Use 'kb retry-assets' to retry.")
