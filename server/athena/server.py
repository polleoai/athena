"""Athena MCP Server — local knowledge base agent.

Exposes all kb commands as MCP tools. The behavioral intelligence
(ingest workflows, cross-referencing, insight rules) lives here,
not in readable skill files.

Usage:
    # Start as stdio server (for Claude Code, Cursor, etc.)
    python -m athena.server /path/to/vault

    # Or via CLI
    athena serve /path/to/vault
"""

import os
import sys
import re
import json
import time
import asyncio
import subprocess
import logging
from mcp.server import Server
from mcp.server.stdio import stdio_server

# Shared Athena config (paths, naming conventions). Single source of truth —
# change bin/config/athena.json, not string literals in code.
_athena_lib = os.path.join(os.path.dirname(os.path.realpath(__file__)), "..", "..", "bin", "lib")
sys.path.insert(0, os.path.abspath(_athena_lib))
from config import raw_dir_for_source_type  # noqa: E402
from mcp.types import Tool, TextContent

logger = logging.getLogger("athena")

# The vault root — set at startup
_vault_root = None
_last_session = None  # loaded on startup from wiki/sessions/
_session_shown = False  # only show session context once per connection


def _load_last_session():
    """Load the most recent session log from wiki/sessions/."""
    sessions_dir = os.path.join(_vault_root, 'wiki', 'sessions')
    if not os.path.isdir(sessions_dir):
        return None
    files = sorted(
        [f for f in os.listdir(sessions_dir) if f.endswith('.md')],
        reverse=True
    )
    if not files:
        return None
    try:
        with open(os.path.join(sessions_dir, files[0]), 'r', encoding='utf-8') as f:
            content = f.read()
        # Extract key sections
        lines = content.split('\n')
        summary = ''
        open_threads = []
        in_section = None
        for line in lines:
            if '## Summary' in line:
                in_section = 'summary'
            elif '## Open Threads' in line:
                in_section = 'threads'
            elif line.startswith('## '):
                in_section = None
            elif in_section == 'summary' and line.strip():
                summary += line.strip() + ' '
            elif in_section == 'threads' and line.strip().startswith('- '):
                open_threads.append(line.strip().lstrip('- '))

        return {
            'file': files[0],
            'summary': summary.strip(),
            'open_threads': open_threads,
        }
    except (IOError, OSError):
        return None


def _get_session_context():
    """Return session context string for first tool call. Empty string after first use."""
    global _session_shown, _last_session
    if _session_shown or not _last_session:
        return ''
    _session_shown = True
    parts = [f'\n--- Session Context ---\nLast session ({_last_session["file"]}):']
    if _last_session.get('summary'):
        parts.append(f'  Summary: {_last_session["summary"]}')
    if _last_session.get('open_threads'):
        parts.append('  Open threads:')
        for t in _last_session['open_threads']:
            parts.append(f'    - {t}')
    parts.append('--- End Session Context ---\n')
    return '\n'.join(parts)


def _check_raw_orphans():
    """Registry-aware orphan detection.

    Compares raw files against wiki raw_path references AND the orphan
    registry (.athena/orphan-registry.json). Returns only NEW orphans
    not already tracked. Also auto-resolves registry entries when a
    wiki page is created for a previously orphan raw file.

    Returns (new_orphans, resolved_count) where new_orphans is a list
    of relative paths not in the registry.
    """
    if not _vault_root:
        return [], 0

    # Load registry
    registry_path = os.path.join(_vault_root, '.athena', 'orphan-registry.json')
    registry = {}
    try:
        with open(registry_path, 'r') as f:
            data = json.load(f)
            registry = data.get('orphans', {})
    except (OSError, json.JSONDecodeError):
        pass

    # Collect all raw files (skip _DO_NOT_WRITE_DIRECTLY.md, assets/)
    raw_dir = os.path.join(_vault_root, 'raw')
    raw_files = set()
    skip_dirs = {'assets'}
    for subdir in os.listdir(raw_dir):
        sub_path = os.path.join(raw_dir, subdir)
        if not os.path.isdir(sub_path) or subdir in skip_dirs:
            continue
        for fname in os.listdir(sub_path):
            if fname.startswith('_') or fname.startswith('.'):
                continue
            raw_files.add(f"raw/{subdir}/{fname}")

    # Collect all raw_path values from wiki pages
    wiki_dir = os.path.join(_vault_root, 'wiki')
    referenced_raws = set()
    for root, dirs, files in os.walk(wiki_dir):
        for fname in files:
            if not fname.endswith('.md'):
                continue
            fpath = os.path.join(root, fname)
            try:
                with open(fpath, 'r', errors='replace') as f:
                    in_frontmatter = False
                    for line in f:
                        line = line.strip()
                        if line == '---':
                            if not in_frontmatter:
                                in_frontmatter = True
                                continue
                            else:
                                break
                        if in_frontmatter and line.startswith('raw_path:'):
                            val = line.split(':', 1)[1].strip().strip('"').strip("'")
                            if val:
                                referenced_raws.add(val)
                            break
            except OSError:
                continue

    # Find all current orphans (raw files with no wiki raw_path reference)
    all_orphans = raw_files - referenced_raws

    # Auto-resolve: registry entries that now have wiki pages
    resolved = 0
    resolved_keys = []
    for rel in list(registry.keys()):
        if rel not in all_orphans and rel in raw_files:
            # This was an orphan but now has a wiki page — resolved
            resolved_keys.append(rel)
            resolved += 1

    # New orphans: not in registry and not referenced
    new_orphans = sorted(o for o in all_orphans if o not in registry)

    # Update registry: remove resolved, add new as needs-fix
    if resolved_keys or new_orphans:
        import time as _t
        for key in resolved_keys:
            del registry[key]
        for orphan in new_orphans:
            registry[orphan] = {
                "status": "needs-fix",
                "reason": "New orphan detected — raw file without wiki page",
                "date_added": _t.strftime('%Y-%m-%d')
            }
        try:
            data = {"_metadata": {"created": "2026-04-13", "description": "Tracks raw files without direct wiki page raw_path references."}, "orphans": registry}
            with open(registry_path, 'w') as f:
                json.dump(data, f, indent=2)
        except OSError:
            pass

    return new_orphans, resolved


def _run_post_lint():
    """Run kb lint after write operations and return a one-line summary."""
    ok, stdout, stderr = _kb_run("lint")
    if not ok:
        return ""
    # Extract summary line
    for line in stdout.split('\n'):
        if 'checks' in line and ('auto-fixed' in line or 'need attention' in line or 'all clear' in line):
            return f"\n\n[lint: {line.strip()}]"
    return ""

def _kb_run(command, *args):
    """Run a kb CLI command and return (success, stdout, stderr).

    This bridges the MCP server to the existing bin/kb commands.
    As we port commands to pure Python, this bridge gets replaced.
    """
    kb_path = os.path.join(_vault_root, 'bin', 'kb')
    cmd = [kb_path, command] + list(args)
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=_vault_root,
            timeout=120,
            env={**os.environ, 'PATH': '/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:' + os.environ.get('PATH', '')}
        )
        return result.returncode == 0, result.stdout.strip(), result.stderr.strip()
    except subprocess.TimeoutExpired:
        return False, '', 'Command timed out'
    except (OSError, subprocess.SubprocessError) as e:
        return False, '', str(e)


def _search_direct(query, top_k=25):
    """Call search.py directly (no subprocess) for performance."""
    search_lib = os.path.join(_vault_root, 'bin', 'lib')
    if search_lib not in sys.path:
        sys.path.insert(0, search_lib)
    from search import search_or_grep
    return search_or_grep(_vault_root, query, top_k)


def _index_direct():
    """Call search index build directly."""
    search_lib = os.path.join(_vault_root, 'bin', 'lib')
    if search_lib not in sys.path:
        sys.path.insert(0, search_lib)
    from search import build_index
    return build_index(_vault_root)


def _index_status_direct():
    """Get index status directly."""
    search_lib = os.path.join(_vault_root, 'bin', 'lib')
    if search_lib not in sys.path:
        sys.path.insert(0, search_lib)
    from search import index_status
    return index_status(_vault_root)


# ── Tool definitions ───────────────────────────────────────────────

TOOLS = [
    Tool(
        name="kb_search",
        description="Search the knowledge base using hybrid search (BM25 + vector + graph). Returns ranked results with titles, summaries, source types, and relevance signals.",
        inputSchema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query — natural language or keywords"},
                "top_k": {"type": "integer", "description": "Max results to return (default 25)", "default": 25},
            },
            "required": ["query"],
        },
    ),
    Tool(
        name="kb_query",
        description="Ask a question about your knowledge base. Searches, reads top pages, and extracts relevant snippets to answer. For full synthesized answers, configure an LLM via kb config.",
        inputSchema={
            "type": "object",
            "properties": {
                "question": {"type": "string", "description": "Question to answer from your knowledge base"},
            },
            "required": ["question"],
        },
    ),
    Tool(
        name="kb_add",
        description="Capture a URL or local file and create a wiki page. Supports: URLs (webpages, GitHub repos, arXiv papers, YouTube videos), local files (PDF, Word, images, text, code), and cloud-synced files (OneDrive, Google Drive, Dropbox paths). Returns the created wiki page path.",
        inputSchema={
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "URL to capture OR local file path (e.g., ~/OneDrive/report.pdf)"},
                "description": {"type": "string", "description": "Optional description hint"},
                "keywords": {"type": "string", "description": "Optional comma-separated keywords"},
            },
            "required": ["url"],
        },
    ),
    Tool(
        name="kb_add_content",
        description="Create a wiki page from pre-fetched content. Use for: (1) kb_add returns needs_browser — fetch via Playwright then pass here, (2) pasting content directly, (3) importing from online workspaces (Google Docs, SharePoint, Notion) — fetch via browser/API then pass here. For documents containing both raw material AND analysis/insights, set has_insights=true and the ingest will extract insights separately.",
        inputSchema={
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "Source URL or identifier (can be a Google Docs URL, SharePoint link, etc.)"},
                "title": {"type": "string", "description": "Page title"},
                "content": {"type": "string", "description": "The page content (text/markdown)"},
                "source_type": {"type": "string", "description": "Type: webpage, repo, paper, video", "default": "webpage"},
                "has_insights": {"type": "boolean", "description": "If true, content may contain pre-existing analysis/insights that should be extracted as separate insight pages", "default": False},
            },
            "required": ["url", "title", "content"],
        },
    ),
    Tool(
        name="kb_index",
        description="Build or rebuild the search index. Run after adding sources to enable ranked search.",
        inputSchema={
            "type": "object",
            "properties": {
                "status_only": {"type": "boolean", "description": "If true, return index status without rebuilding", "default": False},
            },
        },
    ),
    Tool(
        name="kb_stats",
        description="Show knowledge base statistics: source counts, wiki page counts, pending items.",
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="kb_lint",
        description="Run health checks on the knowledge base. Finds broken links, orphan pages, tag issues, index drift, and search index health.",
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="kb_create",
        description="Create a new topic, insight, or project page.",
        inputSchema={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Name for the new page"},
                "topic": {"type": "boolean", "description": "Create as a topic page"},
                "project": {"type": "boolean", "description": "Create as a project page"},
                "goal": {"type": "string", "description": "Project goal (only with project=true)"},
                "description": {"type": "string", "description": "Brief description"},
            },
            "required": ["name"],
        },
    ),
    Tool(
        name="kb_move",
        description="Move wiki pages into a hub/topic. Auto-detects source hub and updates all wikilinks.",
        inputSchema={
            "type": "object",
            "properties": {
                "pages": {"type": "array", "items": {"type": "string"}, "description": "Page names to move"},
                "into": {"type": "string", "description": "Target hub page name"},
            },
            "required": ["pages", "into"],
        },
    ),
    Tool(
        name="kb_rename",
        description="Rename a wiki page and update all wikilinks across the KB.",
        inputSchema={
            "type": "object",
            "properties": {
                "page": {"type": "string", "description": "Current page name"},
                "new_name": {"type": "string", "description": "New page name"},
            },
            "required": ["page", "new_name"],
        },
    ),
    Tool(
        name="kb_remove",
        description="Soft-delete a wiki page to trash (recoverable for 30 days via kb_undo).",
        inputSchema={
            "type": "object",
            "properties": {
                "page": {"type": "string", "description": "Page name to remove"},
                "with_raw": {"type": "boolean", "description": "Also trash the raw source file", "default": False},
            },
            "required": ["page"],
        },
    ),
    Tool(
        name="kb_merge",
        description="Merge multiple wiki pages into one with sections. Old pages are soft-deleted.",
        inputSchema={
            "type": "object",
            "properties": {
                "pages": {"type": "array", "items": {"type": "string"}, "description": "Pages to merge"},
                "into": {"type": "string", "description": "Name for the merged page"},
            },
            "required": ["pages"],
        },
    ),
    Tool(
        name="kb_ungroup",
        description="Dissolve a hub page — moves children to another hub or leaves them independent.",
        inputSchema={
            "type": "object",
            "properties": {
                "hub": {"type": "string", "description": "Hub page to dissolve"},
            },
            "required": ["hub"],
        },
    ),
    Tool(
        name="kb_undo",
        description="Restore the most recently trashed/modified files. Reverses the last destructive operation.",
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="kb_trash",
        description="List items currently in the trash (soft-deleted, recoverable).",
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="kb_purge",
        description="Permanently delete trash items older than 30 days. URLs are preserved in the ledger for future recapture.",
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="kb_journal",
        description="Write a timestamped learning journal entry. Links to relevant wiki pages automatically.",
        inputSchema={
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Journal entry text"},
                "recent": {"type": "boolean", "description": "Show recent journal entries instead of writing", "default": False},
            },
        },
    ),
    Tool(
        name="kb_insight",
        description="Save a polished finding as a permanent wiki page with evidence and rules.",
        inputSchema={
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Insight title"},
                "list": {"type": "boolean", "description": "List existing insights instead of creating", "default": False},
            },
        },
    ),
    Tool(
        name="kb_reflect",
        description="Gather journal entries, session logs, and related pages, then analyze for patterns and propose insights. Use 'focus' to guide analysis toward a specific topic or question. Returns structured data for you to find cross-entry patterns, contradictions, and hidden connections. After analysis, ask the user which insights to save via kb_insight.",
        inputSchema={
            "type": "object",
            "properties": {
                "focus": {"type": "string", "description": "Guide analysis toward a specific topic or question (e.g., 'how do ML courses explain gradient descent differently')"},
                "days": {"type": "integer", "description": "How many days back to look (default: 7)", "default": 7},
                "deep": {"type": "boolean", "description": "Also search index for related pages (slower but finds more connections)", "default": False},
            },
        },
    ),
    Tool(
        name="kb_list",
        description="List wiki pages by type. Use --insights, --topics, --entities, --repos, --papers, --videos, --webpages, --images, --comparisons, --journal, or --recent. No filter lists all pages.",
        inputSchema={
            "type": "object",
            "properties": {
                "filter": {
                    "type": "string",
                    "enum": ["insights", "topics", "entities", "repos", "papers", "videos", "webpages", "images", "comparisons", "projects", "journal", "recent"],
                    "description": "Filter by page type, or 'recent' for last 20 additions",
                },
            },
        },
    ),
    Tool(
        name="kb_rules",
        description="View or add processing rules. 'kb rules' shows all rules. 'kb rules add \"text\"' adds a new rule to the appropriate section.",
        inputSchema={
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["show", "add"], "default": "show"},
                "rule": {"type": "string", "description": "Rule text to add (only with action=add)"},
                "section": {"type": "string", "description": "Show rules for a specific section only"},
            },
        },
    ),
    Tool(
        name="kb_export",
        description="Generate charts, slides, or structured exports from KB data. Supports Mermaid (Obsidian-native), CSV, JSON, and optional PPTX/PNG via Marp/matplotlib.",
        inputSchema={
            "type": "object",
            "properties": {
                "template": {
                    "type": "string",
                    "enum": ["comparison_bar", "distribution_pie", "feature_matrix",
                             "topic_slides", "roadmap_gantt", "relationship_graph",
                             "timeline", "stats_summary", "table_export"],
                    "description": "Visualization template",
                },
                "source": {"type": "string", "description": "Data source: page path, 'kb_stats', or 'tag:ml' filter"},
                "format": {"type": "string", "enum": ["mermaid", "slides_md", "csv", "json"], "default": "mermaid"},
            },
            "required": ["template", "source"],
        },
    ),
]


# ── Tool handlers ──────────────────────────────────────────────────

async def handle_tool(name, arguments):
    """Dispatch MCP tool calls to the appropriate handler."""

    # Prepend session context to the first tool response
    session_ctx = _get_session_context()

    if name == "kb_search":
        query = arguments.get("query", "")
        top_k = arguments.get("top_k", 25)
        ok, results = _search_direct(query, top_k)
        if not ok:
            return [TextContent(type="text", text=f"Search error: {results}")]
        if not results:
            return [TextContent(type="text", text=f"No results for: {query}")]
        lines = [f'Search: "{query}" — {len(results)} results\n']
        for i, r in enumerate(results, 1):
            signals = ', '.join(r.get('signals', []))
            lines.append(f"{i}. [[{r['title']}]]")
            if r.get('summary'):
                lines.append(f"   {r['summary']}")
            lines.append(f"   [{r.get('source_type', '')}] [{signals}]")
            lines.append("")
        result_text = session_ctx + '\n'.join(lines)
        # Piggyback orphan detection on search (registry-aware)
        new_orphans, resolved = _check_raw_orphans()
        if new_orphans:
            result_text += f"\n⚠ {len(new_orphans)} new raw source(s) have no wiki page. Run `kb batch` to complete their ingest."
        return [TextContent(type="text", text=result_text)]

    elif name == "kb_query":
        question = arguments.get("question", "")
        ok, stdout, stderr = _kb_run("query", question)
        return [TextContent(type="text", text=stdout if ok else f"Error: {stderr or stdout}")]

    elif name == "kb_add":
        url = arguments.get("url", "")

        # URL dedup check — before capture, check if URL already has a wiki page
        if url:
            # Normalize URL for comparison (strip trailing slash, query params for tweets)
            norm_url = url.rstrip('/')
            wiki_dir_chk = os.path.join(_vault_root, 'wiki')
            for root_c, dirs_c, files_c in os.walk(wiki_dir_chk):
                for wf in files_c:
                    if not wf.endswith('.md'): continue
                    wfp = os.path.join(root_c, wf)
                    try:
                        with open(wfp, 'r', encoding='utf-8') as f:
                            head = f.read(2000)
                        # Check both url: and urls: fields
                        if norm_url in head or url in head:
                            existing_name = os.path.splitext(wf)[0]
                            return [TextContent(type="text", text=f"Already in KB. Wiki page: [[{existing_name}]]")]
                    except: pass

        args = [url]
        desc = arguments.get("description", "")
        if desc:
            args.extend(["--desc", desc])
        keywords = arguments.get("keywords", "")
        if keywords:
            args.extend(["--keywords", keywords])
        ok, stdout, stderr = _kb_run("add", *args)

        # If raw file already exists, find the wiki page that references it
        if not ok and 'already exists' in stderr:
            raw_path_match = re.search(r'already exists: (.+)', stderr)
            if raw_path_match:
                existing_raw = raw_path_match.group(1).strip()
                abs_raw = os.path.join(_vault_root, existing_raw) if not os.path.isabs(existing_raw) else existing_raw

                # Check for thin content — re-capture if needed
                if os.path.exists(abs_raw):
                    with open(abs_raw, 'r', encoding='utf-8') as f:
                        content = f.read()
                    parts = content.split('## Content', 1)
                    body = parts[1].strip() if len(parts) > 1 else ''
                    if len(body) < 100:
                        os.remove(abs_raw)
                        ok2, stdout2, stderr2 = _kb_run("add", *args)
                        if ok2:
                            return [TextContent(type="text", text=stdout2 + "\n(Re-captured: previous raw file had insufficient content)")]
                        return [TextContent(type="text", text=stdout2 if ok2 else f"Re-capture failed: {stderr2 or stdout2}")]

                # Find wiki page referencing this raw file (by raw_path or url)
                rel_raw = os.path.relpath(abs_raw, _vault_root) if os.path.isabs(existing_raw) else existing_raw
                wiki_dir = os.path.join(_vault_root, 'wiki')
                wiki_page = None
                for root, dirs, files in os.walk(wiki_dir):
                    for wf in files:
                        if not wf.endswith('.md'):
                            continue
                        wf_path = os.path.join(root, wf)
                        try:
                            with open(wf_path, 'r', encoding='utf-8') as f:
                                wf_content = f.read(2000)
                            if rel_raw in wf_content or url in wf_content:
                                wiki_page = os.path.splitext(wf)[0]
                                break
                        except:
                            pass
                    if wiki_page:
                        break

                if wiki_page:
                    return [TextContent(type="text", text=f"Already in KB. Wiki page: [[{wiki_page}]]")]
                else:
                    return [TextContent(type="text", text=f"Raw source exists at {rel_raw} but no wiki page found. Run ingest to create one.")]

        # Detect if browser is needed (curl failed on JS-rendered page)
        if not ok and ('already exists' not in stderr):
            js_domains = ['x.com/i/article', 'linkedin.com', 'reddit.com']
            if any(d in url for d in js_domains) or 'empty' in stderr.lower() or len(stdout) < 50:
                return [TextContent(type="text", text=json.dumps({
                    "status": "needs_browser",
                    "url": url,
                    "message": f"This page requires browser rendering. Use Playwright to navigate to {url}, get the page content, then call kb_add_content with the extracted text."
                }))]
        result_text = stdout if ok else f"Error: {stderr or stdout}"

        # Detect thin content — ask user for help
        if ok and 'THIN_CONTENT' in stdout:
            result_text = (
                f"The URL was captured but the content is very thin (likely requires authentication).\n\n"
                f"**Please help by doing one of these:**\n"
                f"1. Open {url} in your browser, select all the text, copy it, and paste it here. "
                f"I'll create the wiki page from your paste.\n"
                f"2. If you have Obsidian Web Clipper, clip the page — it will appear in the inbox.\n"
                f"3. Say 'skip' to move on.\n\n"
                f"Raw file saved at: {stdout.split('Saved: ')[-1].split(chr(10))[0] if 'Saved:' in stdout else 'raw/'}"
            )
            return [TextContent(type="text", text=result_text)]

        if ok:
            result_text += _run_post_lint()
        return [TextContent(type="text", text=result_text)]

    elif name == "kb_add_content":
        url = arguments.get("url", "")
        title = arguments.get("title", "Untitled")
        content = arguments.get("content", "")
        source_type = arguments.get("source_type", "webpage")

        if not content:
            return [TextContent(type="text", text="Error: content is required")]

        # URL dedup check — prevent creating duplicate pages
        if url:
            wiki_dir_check = os.path.join(_vault_root, 'wiki')
            for root_d, dirs_d, files_d in os.walk(wiki_dir_check):
                for wf in files_d:
                    if not wf.endswith('.md'): continue
                    wfp = os.path.join(root_d, wf)
                    try:
                        with open(wfp, 'r', encoding='utf-8') as f:
                            head = f.read(2000)
                        if url in head:
                            existing_name = os.path.splitext(wf)[0]
                            return [TextContent(type="text", text=f"Already in KB. Wiki page: [[{existing_name}]]")]
                    except: pass

        import time as _time
        # Route through the canonical writer — same enforcement that
        # _process_clip uses, so kb_add_content and Web Clipper produce
        # structurally identical raw pages.
        from pathlib import Path as _Path
        sys.path.insert(0, os.path.join(_vault_root, 'bin', 'lib'))
        from wiki_schema import write_raw_page  # type: ignore
        try:
            raw_path_obj = write_raw_page(
                vault=_Path(_vault_root),
                source_type=source_type,
                url=url or f"local://kb_add_content/{title}",
                title=title,
                body=content,
                extra_frontmatter={
                    'clipped_via': 'kb_add_content',
                    'clipped_at': _time.strftime('%Y-%m-%d'),
                },
            )
        except ValueError as e:
            return [TextContent(type="text", text=f"Error writing raw page: {e}")]
        raw_path = str(raw_path_obj)

        # Auto-generate tags from content keywords
        content_lower = content.lower()
        tag_map = {
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
        auto_tags = set()
        auto_tags.add(source_type)  # always include source type as tag
        for tag, keywords in tag_map.items():
            if any(kw in content_lower for kw in keywords):
                auto_tags.add(tag)
        tags_str = ', '.join(sorted(auto_tags))

        # Find related pages via search index
        related_pages = []
        # Find related pages via keyword match against topic pages (fast, no model loading)
        try:
            import glob as _glob
            title_words = set(re.sub(r'[^\w\s]', '', title.lower()).split()) - {'the', 'a', 'an', 'of', 'for', 'and', 'in', 'on', 'to', 'with', 'tweet'}
            for wf in _glob.glob(os.path.join(_vault_root, 'wiki', 'topics', '*.md')):
                topic_name = os.path.splitext(os.path.basename(wf))[0]
                topic_words = set(topic_name.lower().split()) - {'the', 'a', 'an', 'of', 'for', 'and', 'in', 'on', 'to', 'with'}
                if len(title_words & topic_words) >= 1:
                    related_pages.append(topic_name)
        except: pass

        # Route through canonical wiki writer.
        from pathlib import Path as _Path
        from wiki_schema import write_wiki_page  # type: ignore

        body = re.sub(r'^# .+$', '', content, flags=re.MULTILINE).strip()
        summary = re.sub(r'#\s+\S+', '', body[:200]).replace('\n', ' ').strip().replace('"', "'")
        if len(body) > 200:
            summary = summary[:197] + '...'
        rel_raw = os.path.relpath(raw_path, _vault_root)

        body_text = f"[Source]({url})\n\n{body[:5000]}\n"
        if auto_tags:
            body_text += "\n## Keywords\n"
            body_text += " · ".join(f"[[{t}]]" for t in sorted(auto_tags)) + "\n"

        try:
            wiki_path_obj = write_wiki_page(
                vault=_Path(_vault_root),
                source_type=source_type,
                title=title,
                summary=summary,
                body=body_text,
                tags=sorted(auto_tags),
                related=related_pages,
                raw_path=rel_raw,
                url=url,
            )
            wiki_path = str(wiki_path_obj)
        except ValueError as e:
            return [TextContent(type="text", text=f"Error writing wiki page: {e}")]

        # Update search index
        try:
            search_lib = os.path.join(_vault_root, 'bin', 'lib')
            if search_lib not in sys.path:
                sys.path.insert(0, search_lib)
            from search import update_index
            update_index(_vault_root)
        except Exception:
            pass

        result = f"Created: {os.path.relpath(wiki_path, _vault_root)}\nRaw: {rel_raw}\nTitle: {clean_title}"
        result += _run_post_lint()
        return [TextContent(type="text", text=result)]

    elif name == "kb_index":
        if arguments.get("status_only"):
            info = _index_status_direct()
            lines = ["Search Index Status:"]
            for k, v in info.items():
                lines.append(f"  {k}: {v}")
            return [TextContent(type="text", text='\n'.join(lines))]
        ok, msg = _index_direct()
        return [TextContent(type="text", text=msg)]

    elif name == "kb_stats":
        ok, stdout, stderr = _kb_run("stats")
        text = stdout if ok else f"Error: {stderr}"
        # Piggyback orphan detection (registry-aware)
        new_orphans, resolved = _check_raw_orphans()
        if resolved:
            text += f"\n\n✓ {resolved} previously orphaned raw file(s) now have wiki pages (auto-resolved)."
        if new_orphans:
            text += f"\n\n⚠ {len(new_orphans)} NEW raw source(s) have no wiki page:"
            for o in new_orphans[:10]:
                text += f"\n  - {o}"
            if len(new_orphans) > 10:
                text += f"\n  ... and {len(new_orphans) - 10} more"
            text += "\nRun `kb batch` or say 'athena, process inbox' to complete their ingest."
        return [TextContent(type="text", text=text)]

    elif name == "kb_rules":
        action = arguments.get("action", "show")
        if action == "add":
            rule = arguments.get("rule", "")
            if not rule:
                return [TextContent(type="text", text="Error: rule text is required")]
            ok, stdout, stderr = _kb_run("rules", "add", rule)
            return [TextContent(type="text", text=stdout if ok else f"Error: {stderr or stdout}")]
        else:
            section = arguments.get("section", "")
            args = ["--section", section] if section else []
            ok, stdout, stderr = _kb_run("rules", *args)
            return [TextContent(type="text", text=stdout if ok else f"Error: {stderr or stdout}")]

    elif name == "kb_lint":
        ok, stdout, stderr = _kb_run("lint")
        return [TextContent(type="text", text=stdout if ok else f"Error: {stderr}")]

    elif name == "kb_list":
        filter_type = arguments.get("filter", "")
        valid_filters = {"insights", "topics", "entities", "repos", "papers", "videos", "webpages", "images", "comparisons", "projects", "journal", "recent"}
        if filter_type and filter_type not in valid_filters:
            return [TextContent(type="text", text=f"Unknown filter: {filter_type}. Valid: {', '.join(sorted(valid_filters))}")]
        args = [f"--{filter_type}"] if filter_type else []
        ok, stdout, stderr = _kb_run("list", *args)
        return [TextContent(type="text", text=stdout if ok else f"Error: {stderr or stdout}")]

    elif name == "kb_create":
        n = arguments.get("name", "")
        args = [n]
        if arguments.get("project"):
            args.append("--project")
            g = arguments.get("goal", "")
            if g:
                args.extend(["--goal", g])
        elif arguments.get("topic"):
            args.append("--topic")
        desc = arguments.get("description", "")
        if desc:
            args.extend(["--desc", desc])
        args.append("--yes")
        ok, stdout, stderr = _kb_run("create", *args)
        result_text = stdout if ok else f"Error: {stderr or stdout}"
        if ok: result_text += _run_post_lint()
        return [TextContent(type="text", text=result_text)]

    elif name == "kb_move":
        pages = arguments.get("pages", [])
        into = arguments.get("into", "")
        args = pages + ["--into", into, "--yes"]
        ok, stdout, stderr = _kb_run("move", *args)
        result_text = stdout if ok else f"Error: {stderr or stdout}"
        if ok: result_text += _run_post_lint()
        return [TextContent(type="text", text=result_text)]

    elif name == "kb_rename":
        page = arguments.get("page", "")
        new_name = arguments.get("new_name", "")
        ok, stdout, stderr = _kb_run("rename", page, "--to", new_name, "--yes")
        result_text = stdout if ok else f"Error: {stderr or stdout}"
        if ok: result_text += _run_post_lint()
        return [TextContent(type="text", text=result_text)]

    elif name == "kb_remove":
        page = arguments.get("page", "")
        args = [page, "--yes"]
        if arguments.get("with_raw"):
            args.append("--with-raw")
        ok, stdout, stderr = _kb_run("remove", *args)
        result_text = stdout if ok else f"Error: {stderr or stdout}"
        if ok: result_text += _run_post_lint()
        return [TextContent(type="text", text=result_text)]

    elif name == "kb_merge":
        pages = arguments.get("pages", [])
        args = list(pages)
        into = arguments.get("into", "")
        if into:
            args.extend(["--into", into])
        args.append("--yes")
        ok, stdout, stderr = _kb_run("merge", *args)
        result_text = stdout if ok else f"Error: {stderr or stdout}"
        if ok: result_text += _run_post_lint()
        return [TextContent(type="text", text=result_text)]

    elif name == "kb_ungroup":
        hub = arguments.get("hub", "")
        ok, stdout, stderr = _kb_run("ungroup", hub, "--yes")
        result_text = stdout if ok else f"Error: {stderr or stdout}"
        if ok: result_text += _run_post_lint()
        return [TextContent(type="text", text=result_text)]

    elif name == "kb_undo":
        ok, stdout, stderr = _kb_run("undo")
        return [TextContent(type="text", text=stdout if ok else f"Error: {stderr or stdout}")]

    elif name == "kb_trash":
        ok, stdout, stderr = _kb_run("trash")
        return [TextContent(type="text", text=stdout if ok else f"Error: {stderr or stdout}")]

    elif name == "kb_purge":
        ok, stdout, stderr = _kb_run("purge", "--yes")
        return [TextContent(type="text", text=stdout if ok else f"Error: {stderr or stdout}")]

    elif name == "kb_journal":
        if arguments.get("recent"):
            ok, stdout, stderr = _kb_run("journal", "--recent")
        else:
            text = arguments.get("text", "")
            ok, stdout, stderr = _kb_run("journal", text)
        return [TextContent(type="text", text=stdout if ok else f"Error: {stderr or stdout}")]

    elif name == "kb_insight":
        if arguments.get("list"):
            ok, stdout, stderr = _kb_run("insight", "--list")
        else:
            title = arguments.get("title", "")
            ok, stdout, stderr = _kb_run("insight", title)
        return [TextContent(type="text", text=stdout if ok else f"Error: {stderr or stdout}")]

    elif name == "kb_reflect":
        days = arguments.get("days", 7)
        deep = arguments.get("deep", False)
        focus = arguments.get("focus")
        try:
            import sys
            lib_dir = os.path.join(_vault_root, 'bin', 'lib')
            if lib_dir not in sys.path:
                sys.path.insert(0, lib_dir)
            from reflect import build_reflect_report, format_report_text
            report = build_reflect_report(_vault_root, days=days, deep=deep, focus=focus)
            text = format_report_text(report)
            return [TextContent(type="text", text=text)]
        except Exception as e:
            return [TextContent(type="text", text=f"Error running reflect: {e}")]

    elif name == "kb_export":
        template = arguments.get("template", "")
        source = arguments.get("source", "")
        fmt = arguments.get("format", "mermaid")
        ok, stdout, stderr = _kb_run("export", template, source, "--format", fmt)
        return [TextContent(type="text", text=stdout if ok else f"Error: {stderr or stdout}")]

    else:
        return [TextContent(type="text", text=f"Unknown tool: {name}")]


# ── Server setup ───────────────────────────────────────────────────

def create_server(vault_root):
    """Create and configure the Athena MCP server."""
    global _vault_root, _last_session, _session_shown
    _vault_root = os.path.realpath(vault_root)
    _session_shown = False

    if not os.path.isdir(_vault_root):
        raise ValueError(f"Vault not found: {_vault_root}")

    # Load last session for continuity
    _last_session = _load_last_session()
    if _last_session:
        logger.info(f"Loaded last session: {_last_session['file']}")

    app = Server("athena")

    @app.list_tools()
    async def list_tools():
        return TOOLS

    @app.call_tool()
    async def call_tool(name, arguments):
        return await handle_tool(name, arguments or {})

    return app


# ── Clippings watcher ──────────────────────────────────────────────

SOCIAL_DOMAINS = {'x.com', 'twitter.com', 'linkedin.com', 'reddit.com',
                  'threads.net', 'mastodon.social'}
# Watch ALL configured clipping directories — Web Clipper users may
# drop into the top-level `clippings/` (default) or `inbox/clippings/`
# (older convention). Reads the canonical list from
# bin/config/athena.default.json's `inbox.clippings_watch_default`.
def _resolve_clipping_dirs():
    try:
        import sys as _sys
        _here = os.path.dirname(os.path.abspath(__file__))
        _sys.path.insert(0, os.path.join(_here, '..', '..', 'bin', 'lib'))
        from config import _cfg  # type: ignore
        dirs = _cfg().get('inbox', {}).get('clippings_watch_default', [])
        if dirs:
            return list(dirs)
    except Exception:
        pass
    # Fallback if config unavailable.
    return ['clippings', 'inbox/clippings', 'inbox/Clippings']

CLIPPINGS_DIRS = _resolve_clipping_dirs()
CLIPPINGS_DIR = CLIPPINGS_DIRS[0] if CLIPPINGS_DIRS else 'inbox/clippings'  # back-compat
POLL_INTERVAL = 10  # seconds — only checks directory mtime, not file listing


def _is_social_url(url):
    """Check if a URL is from a social media platform."""
    try:
        from urllib.parse import urlparse
        host = urlparse(url).hostname or ''
        return any(host.endswith(d) for d in SOCIAL_DOMAINS)
    except (ValueError, AttributeError):
        return False


def _parse_clip_frontmatter(filepath):
    """Parse frontmatter from a Web Clipper file. Returns (url, title, content)."""
    try:
        with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
            text = f.read()
    except (IOError, OSError):
        return None, None, ''

    if not text.startswith('---'):
        return None, None, text

    end = text.find('\n---', 3)
    if end == -1:
        return None, None, text

    yaml_block = text[4:end]
    body = text[end + 4:].strip()

    url = None
    title = None
    for line in yaml_block.split('\n'):
        # Web Clipper uses 'source:' or 'url:' for the original URL
        m = re.match(r'^(?:source|url)\s*:\s*["\']?(https?://[^\s"\']+)', line)
        if m:
            url = m.group(1)
        m = re.match(r'^title\s*:\s*["\']?(.+?)["\']?\s*$', line)
        if m:
            title = m.group(1)

    return url, title, body


def _process_clip(vault_root, filepath):
    """Process a single Web Clipper file.

    - Social media URLs → run kb add (follows links)
    - Regular URLs → move to raw/webpages/, content already captured
    """
    url, title, body = _parse_clip_frontmatter(filepath)
    fname = os.path.basename(filepath)

    if not url:
        logger.warning(f"Clip has no URL, skipping: {fname}")
        return False

    if _is_social_url(url):
        # Social media — extract links from clip body and follow them
        logger.info(f"Social clip detected: {fname}")

        # Extract all URLs from the clip body
        urls_in_body = re.findall(r'https?://[^\s\)\]>"\']+', body)

        # Dedup by canonical form so http vs https, www. prefix, trailing
        # slash, and tracking params don't produce N copies of the same
        # link in the "## Links Found" section. User-reported on h100envy:
        # 47 entries collapsed to ~15 unique sources after canonical dedup.
        try:
            sys.path.insert(0, os.path.join(vault_root, 'bin', 'lib'))
            from url_canonical import canonicalize as _canonicalize  # type: ignore
        except Exception:
            _canonicalize = None

        def _canon_key(u: str) -> str:
            """Return the canonical-form key for dedup. Strips trailing
            slash so `https://docs.claude.com/` and `https://docs.claude.com`
            collapse to one entry. Falls back to a coarse lowercase if
            the url_canonical module isn't available."""
            if _canonicalize is not None:
                try:
                    return _canonicalize(u).url.rstrip('/')
                except Exception:
                    pass
            return u.lower().rstrip('/')

        # Filter to external links (not the social platform itself).
        # Keep the FIRST occurrence URL as displayed; track canonical
        # forms in a set for dedup.
        external_urls = []
        seen_canon: set[str] = set()
        for u in urls_in_body:
            u = u.rstrip('.,;:')
            if _is_social_url(u):
                continue
            key = _canon_key(u)
            if key in seen_canon:
                continue
            seen_canon.add(key)
            external_urls.append(u)

        # Also find t.co links specifically (common in tweets) — they
        # short-link to URLs the body might not include in expanded form.
        tco_links = re.findall(r'https?://t\.co/\w+', body)
        for tco in tco_links:
            key = _canon_key(tco)
            if key in seen_canon:
                continue
            seen_canon.add(key)
            external_urls.append(tco)

        if external_urls:
            # Apply auto-follow skip patterns from config (clip.auto_follow_*).
            # Skips marketing/landing/login pages by default — prevents
            # accidental "Web: Service Landing Page" wiki noise.
            try:
                sys.path.insert(0, os.path.join(vault_root, 'bin', 'lib'))
                from config import load as load_config  # type: ignore
                cfg = load_config()
                clip_cfg = cfg.get('clip', {}) if isinstance(cfg, dict) else {}
                max_links = int(clip_cfg.get('auto_follow_max_links', 5))
                skip_patterns = [re.compile(p) for p in clip_cfg.get('auto_follow_skip_patterns', [])]
            except Exception as exc:
                logger.warning(f"clip config load failed; using defaults: {exc}")
                max_links = 5
                skip_patterns = []

            def _should_skip(u: str) -> bool:
                return any(p.search(u) for p in skip_patterns)

            kept = [u for u in external_urls if not _should_skip(u)]
            skipped = [u for u in external_urls if _should_skip(u)]
            if skipped:
                logger.info(f"Skipping {len(skipped)} URLs per clip.auto_follow_skip_patterns: "
                            f"{[s[:80] for s in skipped]}")
            logger.info(f"Found {len(external_urls)} links in social clip "
                        f"({len(kept)} kept, {len(skipped)} skipped), following kept ones")
            for ext_url in kept[:max_links]:
                logger.info(f"  Following: {ext_url}")
                _kb_run("add", ext_url)

        # Save the clip itself as a raw source via the canonical writer.
        from pathlib import Path as _Path
        sys.path.insert(0, os.path.join(vault_root, 'bin', 'lib'))
        from wiki_schema import write_raw_page  # type: ignore
        social_body = body or ''
        if external_urls:
            social_body += "\n\n## Links Found\n\n" + "\n".join(f"- {u}" for u in external_urls) + "\n"
        try:
            raw_path_obj = write_raw_page(
                vault=_Path(vault_root),
                source_type='webpage',
                url=url,
                title=title or fname.replace('.md', ''),
                body=social_body,
                extra_frontmatter={
                    'clipped_via': 'web-clipper-social',
                    'clipped_at': time.strftime('%Y-%m-%d'),
                    'external_urls': external_urls,
                },
            )
            logger.info(f"Processed social clip: {fname} → {raw_path_obj.relative_to(_Path(vault_root))} ({len(external_urls)} links followed)")
        except (IOError, OSError, ValueError) as e:
            logger.error(f"Failed to write social clip {fname}: {e}")
            return False

        # Discover canonical sources mentioned in the social-post body
        # (arXiv IDs, DOIs, GitHub repos, Substack/Medium articles) and
        # auto-queue them to inbox/url-new.txt. The next ingest pass picks
        # them up — user doesn't have to find "the real source" by hand.
        # Tier 1 (regex over text) handles 80%+ of academic posts because
        # LinkedIn/X include OCR'd alt-text in the captured HTML.
        try:
            from canonical_source import discover_canonical_sources, queue_canonical_urls  # type: ignore
            # Image paths for OCR fallback (Tier 2) — pulled from the
            # raw's asset directory if backfill-assets has run.
            image_paths = []
            asset_dir = _Path(vault_root) / 'raw' / 'assets' / raw_path_obj.stem
            if asset_dir.is_dir():
                image_paths = sorted(asset_dir.glob('*.jpg')) + sorted(asset_dir.glob('*.png'))
            urls, tier = discover_canonical_sources(social_body, image_paths)
            if urls:
                queued = queue_canonical_urls(vault_root, url, urls)
                if queued:
                    logger.info(f"Auto-queued {len(queued)} canonical source(s) "
                                f"from social post (tier={tier}): "
                                f"{[u[:60] for u in queued]}")
        except Exception as exc:
            # Never let canonical-source discovery block the ingest.
            logger.warning(f"canonical-source discovery failed for {fname}: {exc}")

        os.remove(filepath)
        return True
    else:
        # Regular webpage — clip IS the raw content, route through the
        # canonical writer so we get URL-derived slug, artifacts/ subdir,
        # schema-validated frontmatter, and a guaranteed H1 in one shot.
        # See bin/lib/wiki_schema.py for the rationale on centralizing this.
        from pathlib import Path as _Path
        try:
            sys.path.insert(0, os.path.join(vault_root, 'bin', 'lib'))
            from wiki_schema import write_raw_page  # type: ignore
            raw_path = str(write_raw_page(
                vault=_Path(vault_root),
                source_type='webpage',
                url=url,
                title=title or fname.replace('.md', ''),
                body=body or '',
                extra_frontmatter={
                    'clipped_via': 'web-clipper',
                    'clipped_at': time.strftime('%Y-%m-%d'),
                },
            ))
            os.remove(filepath)
            logger.info(f"Processed clip: {fname} → {os.path.relpath(raw_path, vault_root)}")

            # Update search index
            try:
                search_lib = os.path.join(vault_root, 'bin', 'lib')
                if search_lib not in sys.path:
                    sys.path.insert(0, search_lib)
                from search import update_index
                update_index(vault_root)
            except Exception:
                pass

            return True
        except (IOError, OSError, ValueError) as e:
            # ValueError covers wiki_schema.SchemaError (subclass) — bad input
            # rejected at write time, which is the whole point of the schema.
            logger.error(f"Failed to process clip {fname}: {e}")
            return False


async def _watch_clippings(vault_root):
    """Background task: watch ALL configured clipping directories for
    new Web Clipper files.

    Pre-fix this only watched a single hardcoded `inbox/clippings/`
    directory, missing files dropped into the user's actual `clippings/`
    folder. Now iterates every directory in `CLIPPINGS_DIRS` (resolved
    from config). Each directory gets its own mtime tracker; one stat
    call per directory per poll.
    """
    last_mtime = {}  # per-directory mtime tracker
    processed = set()  # global cross-directory processed set

    while True:
        try:
            for clip_dir_rel in CLIPPINGS_DIRS:
                clippings_dir = os.path.join(vault_root, clip_dir_rel)
                if not os.path.isdir(clippings_dir):
                    continue
                dir_mtime = os.path.getmtime(clippings_dir)
                prev = last_mtime.get(clippings_dir, 0.0)
                if dir_mtime <= prev:
                    continue
                last_mtime[clippings_dir] = dir_mtime

                for fname in os.listdir(clippings_dir):
                    if not fname.endswith('.md') or fname in processed:
                        continue
                    filepath = os.path.join(clippings_dir, fname)
                    if not os.path.isfile(filepath):
                        continue

                    # Wait for file to finish writing
                    await asyncio.sleep(2)

                    logger.info(f"New clip detected in {clip_dir_rel}: {fname}")
                    _process_clip(vault_root, filepath)
                    processed.add(fname)

        except Exception as e:
            logger.debug(f"Clippings watcher error: {e}")

        await asyncio.sleep(POLL_INTERVAL)


async def main(vault_root):
    """Run the Athena MCP server on stdio with background clippings watcher."""
    app = create_server(vault_root)

    # Start clippings watcher as background task
    watcher = asyncio.create_task(_watch_clippings(vault_root))

    try:
        async with stdio_server() as (read_stream, write_stream):
            await app.run(read_stream, write_stream, app.create_initialization_options())
    finally:
        watcher.cancel()
        try:
            await watcher
        except asyncio.CancelledError:
            pass


if __name__ == "__main__":
    import asyncio
    if len(sys.argv) < 2:
        print("Usage: python -m athena.server /path/to/vault", file=sys.stderr)
        sys.exit(1)
    vault = sys.argv[1]
    asyncio.run(main(vault))
