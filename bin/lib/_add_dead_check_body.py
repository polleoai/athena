import os, sys, re, glob, time, json
from pathlib import Path

KB = Path(sys.argv[1])
NOW = time.time()
FRESH_S = 600  # 10 min

# Dead-page signatures. Each pattern is a regex over title OR body
# that strongly indicates a fetched-chrome page rather than real
# content. Conservative — false positives mean dropping real captures.
DEAD_PATTERNS = [
    (r"page not found\s*[·•]\s*github", "GitHub 404"),
    (r"this account doesn['']?t exist\s*[/·•]\s*x", "X account deleted"),
    (r"hmm[.…]+this page doesn['']?t exist", "X post unavailable"),
    (r"page not found\s*\|\s*linkedin", "LinkedIn 404"),
    (r"^\s*404\s*[—–-]\s*not found\s*$", "Generic 404"),
    (r"this site can'?t be reached", "Connection refused"),
]
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
