import sys, os, re, glob, time

KB = sys.argv[1]
sys.path.insert(0, os.path.join(KB, 'bin', 'lib'))
from config import raw_dir as _raw_dir, raw_categories

# mtime gate: skip raws older than this many minutes unless the legacy
# RECONCILE_ALL env var is set. Catches fresh captures (just-clipped),
# excludes stale orphans (from prior sessions).
_RECONCILE_ALL = bool(os.environ.get('RECONCILE_ALL', ''))
try:
    _MAX_AGE_S = int(os.environ.get('RECONCILE_MAX_AGE_MIN', '10')) * 60
except ValueError:
    _MAX_AGE_S = 600
_now = time.time()

# Build set of all raw file basenames and URLs referenced in wiki pages (read once)
wiki_content = ""
for f in glob.glob(os.path.join(KB, "wiki", "**", "*.md"), recursive=True):
    try:
        with open(f, "r", encoding="utf-8", errors="replace") as fh:
            wiki_content += fh.read(3000) + "\n"
    except: pass

# Check each raw file — walks every configured category's artifact dir.
orphans = []
_md_categories = [name for name, cfg in raw_categories().items()
                  if '.md' in cfg.get('artifact_exts', [])]
for cat in _md_categories:
    full_dir = os.path.join(KB, _raw_dir(cat))
    if not os.path.isdir(full_dir): continue
    for rf in glob.glob(os.path.join(full_dir, "*.md")):
        base = os.path.splitext(os.path.basename(rf))[0]
        if base.startswith("_") or base.startswith(".") or "pbs-twimg" in base or "-format-jpg" in base:
            continue
        # mtime gate: skip stale orphans unless RECONCILE_ALL is set.
        if not _RECONCILE_ALL:
            try:
                if (_now - os.path.getmtime(rf)) > _MAX_AGE_S:
                    continue
            except OSError:
                continue
        # Check if any wiki page references this raw file by name or URL
        if base in wiki_content:
            continue
        # Check by URL
        url = ""
        try:
            with open(rf, "r", encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    if "**URL:**" in line:
                        m = re.search(r"https?://\S+", line)
                        if m: url = m.group(0)
                        break
        except: pass
        if url and url in wiki_content:
            continue
        orphans.append(rf)

for o in orphans:
    print(o)
