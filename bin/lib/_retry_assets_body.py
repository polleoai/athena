import os, sys
from pathlib import Path
KB = Path(sys.argv[1])
sys.path.insert(0, str(KB / 'bin' / 'lib'))
from asset_download import download_assets, IMG_RE

retry = KB / 'inbox' / 'asset-retry.tsv'
if not retry.exists():
    print("Nothing to retry — inbox/asset-retry.tsv does not exist."); sys.exit(0)

lines = retry.read_text(encoding='utf-8').splitlines()
if len(lines) <= 1:
    print("Nothing to retry — file is empty."); sys.exit(0)

# Group failures by page so each page is processed once.
by_page = {}
for ln in lines[1:]:
    parts = ln.split('\t')
    if len(parts) < 2: continue
    by_page.setdefault(parts[0], []).append(parts[1])

# Truncate the retry file; download_assets will re-append any still-failing.
retry.write_text("page_slug\turl\talt\terror\ttried_at\n", encoding='utf-8')

still_failing = 0
for slug, urls in by_page.items():
    raw_path = KB / 'raw' / 'webpages' / 'artifacts' / f"{slug}.md"
    if not raw_path.exists():
        print(f"  skip {slug}: raw page missing"); continue
    text = raw_path.read_text(encoding='utf-8')
    new_text = download_assets(text, slug, KB)
    if new_text != text:
        raw_path.write_text(new_text, encoding='utf-8')
        recovered = sum(1 for u in urls if u not in new_text)
        print(f"  {slug}: recovered {recovered}/{len(urls)}")
    else:
        print(f"  {slug}: 0/{len(urls)} recovered")

# Count what's still in the file after re-runs.
remaining = retry.read_text(encoding='utf-8').splitlines()[1:]
print()
print(f"Done. {len(remaining)} URL(s) still failing — inspect inbox/asset-retry.tsv.")
