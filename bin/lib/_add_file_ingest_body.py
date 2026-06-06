import sys, os, time
sys.path.insert(0, sys.argv[1] + '/bin/lib')
from ingest_file import ingest_local_file, classify_file

kb_root = sys.argv[1]
filepath = os.path.realpath(sys.argv[2])

category, source_type, raw_dir = classify_file(filepath)
if category is None:
    ext = os.path.splitext(filepath)[1]
    print(f"Unsupported file type: {ext}")
    print("Supported: .md .txt .csv .json .py .js .pdf .png .jpg .docx .pptx .xlsx")
    sys.exit(1)

print(f"Detected type: {category}")
print(f"File: {os.path.basename(filepath)}")

result = ingest_local_file(kb_root, filepath)
if not result['success']:
    print(f"Error: {result['error']}")
    sys.exit(1)

print(f"Saved: {result['raw_path']}")
print(f"Title: {result['title']}")

# Route through canonical wiki writer.
import sys as _sys
_sys.path.insert(0, os.path.join(kb_root, 'bin', 'lib'))
from wiki_schema import write_wiki_page, SchemaError  # type: ignore
from pathlib import Path as _Path

summary = result.get('content', '')[:150].replace('\n', ' ').strip()
if len(result.get('content', '')) > 150:
    summary += '...'
tags = {
    'paper': ['paper'],
    'repo': ['tool'],
    'image': ['image'],
}.get(result['source_type'], [result['source_type']])

try:
    wp = write_wiki_page(
        vault=_Path(kb_root),
        source_type=result['source_type'],
        title=result['title'],
        summary=summary or '(see local copy)',
        body=result.get('content', '')[:2000] or '(content not available)',
        tags=tags,
        raw_path=result['raw_path'],
    )
    print(f"Created: {os.path.relpath(str(wp), kb_root)}")
except SchemaError as e:
    s = str(e)
    if 'already exists' in s:
        print(f"Wiki page already exists: {s}")
    else:
        print(f"Error writing wiki page: {e}")
        sys.exit(1)

print()
print("Next step: review the wiki page and add cross-references.")
