import sys, os, re, glob
from pathlib import Path

KB = sys.argv[1]
args = sys.argv[2:]

# ── Parse arguments ──────────────────────────────────
old_name = None
new_name = None
confirmed = False
i = 0
while i < len(args):
    if args[i] in ('--to', '-t'):
        if i + 1 < len(args):
            new_name = args[i + 1]; i += 2; continue
        else:
            print("Error: --to requires a name"); sys.exit(1)
    elif args[i] in ('--yes', '-y'):
        confirmed = True; i += 1
    elif args[i] in ('--help', '-h'):
        print("""Usage: kb rename <page> --to "New Name"

Rename a wiki page and update all references across the KB.

Arguments:
  page             Current page name (exact filename without .md)
  --to, -t         New name for the page
  --yes, -y        Skip confirmation prompt

What it does:
  1. Renames the wiki file
  2. Updates the title in frontmatter
  3. Updates ALL [[wikilinks]] across the KB to the new name
  4. Updates hub tables that reference this page

Examples:
  kb rename "Old Page Name" --to "New Page Name"
""")
        sys.exit(0)
    else:
        if old_name is None:
            old_name = args[i]
        i += 1

if not old_name or not new_name:
    print("Error: need <page> and --to <new name>")
    print('Usage: kb rename <page> --to "New Name"')
    sys.exit(1)

# ── Find the page ────────────────────────────────────
page_path = None
for f in glob.glob(os.path.join(KB, 'wiki', '**', '*.md'), recursive=True):
    if os.path.splitext(os.path.basename(f))[0] == old_name:
        page_path = f
        break

if not page_path:
    print(f"Error: page not found: {old_name}")
    sys.exit(1)

if old_name == new_name:
    print("Error: new name is the same as the current name")
    sys.exit(1)

if '..' in new_name or '/' in new_name or '\\' in new_name:
    print(f"Error: name contains unsafe characters: {new_name}")
    sys.exit(1)

# Check new name doesn't already exist
for f in glob.glob(os.path.join(KB, 'wiki', '**', '*.md'), recursive=True):
    if os.path.splitext(os.path.basename(f))[0] == new_name:
        print(f"Error: a page named '{new_name}' already exists")
        sys.exit(1)

# ── Count references ─────────────────────────────────
ref_count = 0
for f in glob.glob(os.path.join(KB, 'wiki', '**', '*.md'), recursive=True):
    if f == page_path:
        continue
    if os.path.basename(f) in ('.gitkeep', '_TEMPLATE.md'):
        continue
    with open(f, 'r') as fh:
        if f'[[{old_name}]]' in fh.read():
            ref_count += 1

# ── Preview ──────────────────────────────────────────
print(f"Rename: {old_name}")
print(f"    →   {new_name}")
print(f"References to update: {ref_count} pages")
print()

if not confirmed:
    try:
        answer = input("Proceed? (y/N) ").strip().lower()
        if answer != 'y':
            print("Cancelled."); sys.exit(0)
    except (EOFError, KeyboardInterrupt):
        print("\nCancelled."); sys.exit(0)

# ── Snapshot for undo ────────────────────────────────
sys.path.insert(0, os.path.join(KB, 'bin', 'lib'))
from snapshot import snapshot_files

# Find all files that reference this page (will be modified by link updates)
files_to_modify = [page_path]  # the page itself
for f in glob.glob(os.path.join(KB, 'wiki', '**', '*.md'), recursive=True):
    if os.path.basename(f) in ('.gitkeep', '_TEMPLATE.md'):
        continue
    if f == page_path:
        continue
    try:
        with open(f, 'r') as fh:
            if f'[[{old_name}]]' in fh.read():
                files_to_modify.append(f)
    except (IOError, UnicodeDecodeError):
        pass

# Snapshot: treat old file as "deleted" (moved to trash), other files as "modified"
other_files = [f for f in files_to_modify if f != page_path]
batch = snapshot_files(KB, files_to_delete=[page_path], files_to_modify=other_files,
               operation="rename", description=f'kb rename "{old_name}" --to "{new_name}"')

# Write the renamed file (old file is now in trash, read content from snapshot)
new_path = os.path.join(os.path.dirname(page_path), f'{new_name}.md')
rel = os.path.relpath(page_path, KB)
trash_copy = os.path.join(batch, 'deleted', rel)
with open(trash_copy, 'r', encoding='utf-8') as f:
    content = f.read()
print(f"Renamed file: {os.path.basename(page_path)} → {os.path.basename(new_path)}")

# ── Update title in frontmatter ──────────────────────
content = re.sub(
    r'^(title:\s*)"[^"]*"',
    f'\\1"{new_name}"',
    content,
    count=1,
    flags=re.MULTILINE
)
with open(new_path, 'w', encoding='utf-8') as f:
    f.write(content)

# Bump mtime so Obsidian's file-watcher fires a fresh "modify" event,
# which is what Dataview keys off to refresh its per-file metadata cache.
# Without this, a rename leaves Dataview's index pointing at the old
# (deleted) path until Obsidian is restarted — symptom witnessed
# 2026-05-21 when two LinkedIn pages renamed via this command remained
# invisible in the Recently Added dashboard despite all on-disk fields
# being correct. os.utime with None sets both atime+mtime to now.
os.utime(new_path, None)

# Mark the new file as "created" so kb undo can delete it
from snapshot import mark_created
mark_created(KB, batch, new_path)

# ── Update all wikilinks across the KB ───────────────
updated = 0
for f in sorted(glob.glob(os.path.join(KB, 'wiki', '**', '*.md'), recursive=True)):
    if os.path.basename(f) in ('.gitkeep', '_TEMPLATE.md'):
        continue
    with open(f, 'r', encoding='utf-8') as fh:
        content = fh.read()
    original = content

    # Replace [[Old Name]] → [[New Name]]
    content = content.replace(f'[[{old_name}]]', f'[[{new_name}]]')
    content = content.replace(f'"[[{old_name}]]"', f'"[[{new_name}]]"')
    # Handle [[Old Name|display]] → [[New Name|display]]
    content = re.sub(
        r'\[\[' + re.escape(old_name) + r'\|([^\]]+)\]\]',
        f'[[{new_name}|\\1]]',
        content
    )

    if content != original:
        with open(f, 'w', encoding='utf-8') as fh:
            fh.write(content)
        updated += 1

print(f"Updated wikilinks in {updated} files")

# ── Write redirect stub at old path ──────────────────
# Use the canonical write_wiki_stub() — same redirect contract used by
# Athena code anywhere stubs are written. See bin/lib/wiki_schema.py.
# Skip the stub when the OLD name carries Obsidian-forbidden chars (#^[]):
# no valid [[wikilink]] could ever resolve to such a name, so the stub is
# dead weight AND would itself re-trip the forbidden-char lint check —
# defeating a rename whose whole purpose is to retire an unclickable name.
# (This is how the bracketed 'Web: [![Cisco…' redirect tombstone arose.)
_old_name_unclickable = bool(re.search(r'[#^\[\]]', old_name))
if _old_name_unclickable:
    print("Skipped redirect stub: old name has Obsidian-forbidden chars "
          "(#^[]) — no wikilink could target it.")
if '--no-stub' not in args and not _old_name_unclickable:
    from wiki_schema import write_wiki_stub  # type: ignore
    # Infer source_type from the wiki subdirectory we found the page in.
    _stub_source_type = 'webpage'
    _rel = os.path.relpath(page_path, KB).replace('\\', '/')
    for st, frag in (('paper', '/papers/'), ('repo', '/repos/'),
                     ('webpage', '/webpages/'), ('video', '/videos/'),
                     ('image', '/images/'), ('entity', '/entities/'),
                     ('topic', '/topics/'), ('insight', '/insights/'),
                     ('journal', '/journal/')):
        if frag in _rel:
            _stub_source_type = st
            break
    stub_path = str(write_wiki_stub(
        vault=Path(KB), source_type=_stub_source_type,
        old_name=old_name, new_name=new_name,
    ))
    mark_created(KB, batch, stub_path)
    print(f"Wrote redirect stub: {os.path.relpath(stub_path, KB)} → [[{new_name}]]")

print()
print("Done.")
print("Run 'kb lint' to verify.")