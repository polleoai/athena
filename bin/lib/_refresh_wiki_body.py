import sys, os, re, glob

KB = sys.argv[1]
args = sys.argv[2:]

page_name = None
i = 0
while i < len(args):
    if args[i] in ('--help', '-h'):
        print("""Usage: kb refresh-wiki <page>

Re-process the raw source for an existing wiki page and rewrite the
wiki body. Preserves filename + frontmatter (title, summary, tags,
related). Snapshots the prior wiki page to .kb-trash/ for recovery.

Use this after a process_clip / wiki_writer bug fix to replay the
new pipeline against existing pages without re-fetching from the URL.
For re-fetching from the URL, use `kb add <url>` instead (the new
URL-collision check will skip if the page exists).

Arguments:
  page             Existing wiki page name (filename without .md)

Examples:
  kb refresh-wiki "LinkedIn: AI Attack Surface Compounding — ClaudeBleed (Praneeta D)"
""")
        sys.exit(0)
    else:
        if page_name is None:
            page_name = args[i]
        i += 1

if not page_name:
    print("Error: page name required")
    print('Usage: kb refresh-wiki <page>')
    sys.exit(1)

# ── Find the page ────────────────────────────────────
page_path = None
for f in glob.glob(os.path.join(KB, 'wiki', '**', '*.md'), recursive=True):
    if os.path.splitext(os.path.basename(f))[0] == page_name:
        page_path = f
        break

if not page_path:
    print(f"Error: page not found: {page_name}")
    sys.exit(1)

# ── Read existing frontmatter ─────────────────────────
with open(page_path, 'r', encoding='utf-8') as fh:
    existing = fh.read()
m = re.match(r'^---\n(.*?)\n---', existing, re.DOTALL)
if not m:
    print(f"Error: page has no frontmatter: {page_path}")
    sys.exit(1)
fm = m.group(1)

def _scalar(key):
    mm = re.search(rf'^{key}:\s*"?([^"\n]+?)"?\s*$', fm, re.MULTILINE)
    return mm.group(1).strip() if mm else None

def _list(key):
    """Parse a YAML list — supports both flow ([a, b]) and block (- a / - b)."""
    flow = re.search(rf'^{key}:\s*\[(.*?)\]\s*$', fm, re.MULTILINE)
    if flow:
        return [x.strip().strip('"').strip("'") for x in flow.group(1).split(',') if x.strip()]
    block = re.search(rf'^{key}:\s*\n((?:[ \t]+-\s+.*\n?)+)', fm, re.MULTILINE)
    if block:
        return [re.sub(r'^[ \t]+-\s+', '', line).strip().strip('"').strip("'")
                for line in block.group(1).splitlines() if line.strip()]
    return []

raw_path = _scalar('raw_path')
url = _scalar('url') or _scalar('source')
title = _scalar('title')
summary = _scalar('summary') or ''
tags = _list('tags')
related = _list('related')

if not raw_path:
    print(f"Error: page frontmatter has no raw_path — cannot refresh: {page_path}")
    sys.exit(1)
if not os.path.exists(os.path.join(KB, raw_path)):
    print(f"Error: raw file missing on disk: {raw_path}")
    print("  Re-capture from URL with `kb add <url>` instead.")
    sys.exit(1)

# Prefer the raw's `source:` as the authoritative provenance link. The raw is
# immutable truth; the wiki `url:` must mirror it. This heals a corrected raw
# source (e.g. a LinkedIn unservable-canonical /posts/ugcpost-<id> replaced by
# the resolvable original) on refresh instead of persisting the stale wiki url.
try:
    with open(os.path.join(KB, raw_path), 'r', encoding='utf-8', errors='replace') as _rf:
        _raw_head = _rf.read(4096)
    _rs = re.search(r'^source:\s*"?([^"\n]+?)"?\s*$', _raw_head, re.MULTILINE)
    if _rs and _rs.group(1).strip():
        url = _rs.group(1).strip()
except OSError:
    pass

print(f"Refreshing: {os.path.relpath(page_path, KB)}")
print(f"  Raw:   {raw_path}")
print(f"  URL:   {url or '(none)'}")
print(f"  Title: {title}")

# ── Re-run create_wiki_page with overwrite=True ───────
# Construct an llm_result from the existing frontmatter so the title /
# summary / tags / related are preserved verbatim. Body comes fresh
# from process_clip via build_fallback_data inside create_wiki_page.
sys.path.insert(0, os.path.join(KB, 'bin', 'lib'))
from wiki_page import create_wiki_page, preprocess_content  # type: ignore

# Pre-process the raw to get the cleaned body (this is what create_wiki_page
# does internally, but we need to hand it back as `llm_result.body` so the
# fallback path doesn't kick in and overwrite our preserved metadata).
with open(os.path.join(KB, raw_path), 'r', encoding='utf-8', errors='replace') as fh:
    raw_text = fh.read()
parsed = preprocess_content(raw_text)
cleaned_body = parsed['body']

llm_result = {
    'title': title,
    'summary': summary,
    'tags': tags,
    'related': related,
    'body': cleaned_body,
}

# Pre-flight size — for the report at the end.
old_size = os.path.getsize(page_path)

result = create_wiki_page(
    vault_path=KB,
    raw_path=raw_path,
    url=url,
    llm_result=llm_result,
    hint_title=title,
    source='refresh-wiki',
    overwrite=True,
)

if result['status'] == 'created':
    new_size = os.path.getsize(os.path.join(KB, result['wiki_path']))
    delta = new_size - old_size
    sign = '+' if delta >= 0 else ''
    print(f"\nDone. {old_size} → {new_size} bytes ({sign}{delta})")
    print(f"  Snapshot: .kb-trash/<timestamp>_refresh_wiki/{os.path.basename(page_path)}")
    print(f"  Restore: kb undo")
elif result['status'] == 'schema_error':
    print(f"\nError: schema validation failed — original page UNCHANGED")
    print(f"  {result.get('error', '?')}")
    sys.exit(2)
else:
    print(f"\nUnexpected status: {result['status']}")
    print(f"  {result}")
    sys.exit(2)