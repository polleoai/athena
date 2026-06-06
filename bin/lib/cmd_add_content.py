"""`kb add-content` -- ingest already-extracted text as a raw + wiki page.

This is the CLI face of (and single source of truth for) the MCP kb_add_content
tool. The tool used to inline this whole flow inside the MCP server, which (a)
meant the logic was reachable only by importing the server -- not via the shell,
so external programs couldn't trigger it without linking a library -- and (b)
let a latent `clean_title` NameError sit undetected in its success path. The MCP
tool now shells out to `kb add-content`, so there is ONE implementation, reached
two ways, and it is exercised by tests.

Used when a live capture comes back thin (auth-walled pages, etc.) and the
caller already has the real text: it writes the canonical raw page AND a wiki
page, dedups by URL, auto-tags from keywords, links related topic pages, and
reindexes -- structurally identical to what _process_clip / the Web Clipper
path produce.

Interface:
    kb add-content --url <url> --title "<title>" [--source-type <type>]
                   (--content-file <path> | --content - | --content "<text>")
                   [--no-index]

Content resolution precedence: --content-file > --content (literal, unless "-")
> stdin. `--content -` forces stdin. `--no-index` skips the search reindex
(useful for batch external callers and hermetic tests).
"""
from __future__ import annotations

import glob
import os
import re
import sys
import time
from pathlib import Path
from typing import List

# Keyword -> tag map. Kept in sync with the ingest tagger so a pasted page tags
# the same way a captured one would.
_TAG_MAP = {
    'security': ['security', 'vulnerability', 'exploit', 'pentest', 'cve', 'threat'],
    'ai-agents': ['agent', 'agentic', 'autonomous'],
    'llm': ['llm', 'language model', 'gpt', 'claude', 'transformer'],
    'ml': ['machine learning', 'neural network', 'training', 'gradient'],
    'claude-code': ['claude code', 'claude-code', 'mcp server'],
    'prompt-engineering': ['prompt', 'system prompt', 'few-shot'],
    'memory': ['memory', 'knowledge graph', 'rag', 'retrieval'],
    'obsidian': ['obsidian', 'vault', 'wikilink'],
    'finance': ['finance', 'investment', 'portfolio', 'trading'],
}
_STOPWORDS = {'the', 'a', 'an', 'of', 'for', 'and', 'in', 'on', 'to', 'with'}


class AddContentError(Exception):
    """Ingest failed in a way the caller should surface as an error message."""


def _find_existing_by_url(vault: Path, url: str) -> str | None:
    """Return the wiki page name already holding this URL, else None (dedup)."""
    if not url:
        return None
    wiki_dir = vault / 'wiki'
    for root_d, _dirs, files in os.walk(wiki_dir):
        for wf in files:
            if not wf.endswith('.md'):
                continue
            try:
                head = (Path(root_d) / wf).read_text(encoding='utf-8')[:2000]
            except OSError:
                continue
            if url in head:
                return os.path.splitext(wf)[0]
    return None


def _auto_tags(content: str, source_type: str) -> list[str]:
    low = content.lower()
    tags = {source_type}
    for tag, kws in _TAG_MAP.items():
        if any(kw in low for kw in kws):
            tags.add(tag)
    return sorted(tags)


def _related_topics(vault: Path, title: str) -> list[str]:
    title_words = set(re.sub(r'[^\w\s]', '', title.lower()).split()) - (_STOPWORDS | {'tweet'})
    related: list[str] = []
    for wf in glob.glob(str(vault / 'wiki' / 'topics' / '*.md')):
        topic = os.path.splitext(os.path.basename(wf))[0]
        topic_words = set(topic.lower().split()) - _STOPWORDS
        if title_words & topic_words:
            related.append(topic)
    return related


def ingest_content(vault: Path, *, url: str, title: str, content: str,
                   source_type: str = 'webpage',
                   clipped_via: str = 'kb_add_content',
                   reindex: bool = True, today: str | None = None) -> str:
    """Ingest pasted/extracted content as a raw + wiki page. Returns the
    human-readable result message. Raises AddContentError on failure.

    `reindex` and `today` are seams: tests disable reindex (no ollama) and pin
    the date stamp for determinism.
    """
    if not content:
        raise AddContentError("content is required")

    existing = _find_existing_by_url(vault, url)
    if existing:
        return f"Already in KB. Wiki page: [[{existing}]]"

    from wiki_schema import write_raw_page, write_wiki_page  # type: ignore

    stamp = today or time.strftime('%Y-%m-%d')
    raw_path_obj = write_raw_page(
        vault=vault,
        source_type=source_type,
        url=url or f"local://kb_add_content/{title}",
        title=title,
        body=content,
        extra_frontmatter={'clipped_via': clipped_via, 'clipped_at': stamp},
    )
    rel_raw = os.path.relpath(str(raw_path_obj), str(vault))

    tags = _auto_tags(content, source_type)
    related = _related_topics(vault, title)

    body = re.sub(r'^# .+$', '', content, flags=re.MULTILINE).strip()
    summary = re.sub(r'#\s+\S+', '', body[:200]).replace('\n', ' ').strip().replace('"', "'")
    if len(body) > 200:
        summary = summary[:197] + '...'

    body_text = f"[Source]({url})\n\n{body[:5000]}\n"
    if tags:
        body_text += "\n## Keywords\n" + " · ".join(f"[[{t}]]" for t in tags) + "\n"

    wiki_path_obj = write_wiki_page(
        vault=vault,
        source_type=source_type,
        title=title,
        summary=summary,
        body=body_text,
        tags=tags,
        related=related,
        raw_path=rel_raw,
        url=url,
    )
    rel_wiki = os.path.relpath(str(wiki_path_obj), str(vault))

    if reindex:
        # Best-effort, exactly as the MCP path treated it: an index failure
        # (e.g. no embedding backend) must not fail the ingest.
        try:
            from search import update_index  # type: ignore
            update_index(str(vault))
        except Exception:
            pass

    return f"Created: {rel_wiki}\nRaw: {rel_raw}\nTitle: {title}"


def _usage() -> None:
    print('Usage: kb add-content --url <url> --title "<title>" '
          '[--source-type <type>]')
    print('                      (--content-file <path> | --content - | '
          '--content "<text>") [--no-index]')
    print()
    print("Ingest already-extracted text as a raw + wiki page (used when a live")
    print("capture is thin). Content comes from --content-file, --content, or")
    print("stdin (the default, or --content -).")


def handle(argv: List[str], root: str) -> int:
    url = ""
    title = "Untitled"
    source_type = "webpage"
    content: str | None = None
    content_file = ""
    reindex = True

    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg in ("--help", "-h"):
            _usage()
            return 0
        elif arg == "--url":
            i += 1; url = argv[i] if i < len(argv) else ""
        elif arg == "--title":
            i += 1; title = argv[i] if i < len(argv) else title
        elif arg == "--source-type":
            i += 1; source_type = argv[i] if i < len(argv) else source_type
        elif arg == "--content":
            i += 1; content = argv[i] if i < len(argv) else ""
        elif arg == "--content-file":
            i += 1; content_file = argv[i] if i < len(argv) else ""
        elif arg == "--no-index":
            reindex = False
        else:
            sys.stderr.write(f"Unknown flag: {arg}\n")
            return 1
        i += 1

    # Resolve content: --content-file > --content (unless "-") > stdin.
    if content_file:
        try:
            content = Path(content_file).read_text(encoding="utf-8")
        except OSError as e:
            sys.stderr.write(f"Error: cannot read --content-file: {e}\n")
            return 1
    elif content == "-" or content is None:
        content = sys.stdin.read()

    try:
        msg = ingest_content(
            Path(root), url=url, title=title, content=content or "",
            source_type=source_type, reindex=reindex,
        )
    except AddContentError as e:
        sys.stderr.write(f"Error: {e}\n")
        return 1
    except ValueError as e:  # schema-validation failures from the writers
        sys.stderr.write(f"Error writing page: {e}\n")
        return 1
    print(msg)
    return 0
