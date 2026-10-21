import os, sys, re, glob, time, json
from pathlib import Path

KB = Path(sys.argv[1])
NOW = time.time()
FRESH_S = 600  # 10 min

# Dead-page signatures live in bin/config/dead-page-patterns.json — the one
# source shared with kb-add duplicate detection (src/athena/plugin.js
# findDuplicate). Each pattern is a regex over title OR body that strongly
# indicates a fetched-chrome page rather than real content. Conservative —
# false positives mean dropping real captures.
# Bodies run via _heredoc_runner.run_body with NO __file__ in scope, so the
# file is located through config._find_file (the same resolver that finds
# athena.default.json), never via this script's own path.
from config import _find_file
_PATTERNS_FILE = _find_file("bin/config/dead-page-patterns.json")
if _PATTERNS_FILE is None:
    sys.stderr.write("  (dead-check) bin/config/dead-page-patterns.json not found — "
                     "dead-page sweep SKIPPED; error-page captures will not be discarded\n")
    DEAD_PATTERNS = []
else:
    DEAD_PATTERNS = [(e["pattern"], e["label"])
                     for e in json.loads(_PATTERNS_FILE.read_text(encoding="utf-8"))["patterns"]]
DEAD_RE = [(re.compile(p, re.IGNORECASE), label) for p, label in DEAD_PATTERNS]

def is_dead(raw_text: str) -> str | None:
    # Check title (frontmatter) + first 1500 body chars
    head = raw_text[:3000]
    for rx, label in DEAD_RE:
        if rx.search(head):
            return label
    return None

trashed = []
for raw in glob.glob(str(KB / "raw" / "**" / "*.md"), recursive=True):
    p = Path(raw)
    if p.name.startswith("_"):
        continue
    try:
        if (NOW - p.stat().st_mtime) > FRESH_S:
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
    except (IOError, OSError):
        continue
    label = is_dead(text)
    if not label:
        continue
    # Move to .kb-trash
    ts = time.strftime("%Y%m%dT%H%M%S")
    bundle = KB / ".kb-trash" / f"{ts}_dead-page"
    rel = p.relative_to(KB)
    dest = bundle / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        p.rename(dest)
        trashed.append((rel, label))
    except OSError as e:
        sys.stderr.write(f"  (dead-check) couldn't trash {rel}: {e}\n")

if trashed:
    print(f"\n── Discarded {len(trashed)} dead-page capture(s):")
    for rel, label in trashed:
        print(f"  ✗ {rel} ({label})")
