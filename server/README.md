# Athena — Your Second Brain

AI-powered knowledge management with hybrid search. Lives inside your LLM console (Claude Code, Cursor, VS Code) and renders in Obsidian.

## Quick Start

```bash
pip install athena-brain

# Initialize a new vault
athena init ~/my-vault

# Or use with an existing Obsidian vault
cd ~/my-vault
kb index        # Build search index
kb search "machine learning"   # Search
kb query "how many repos?"     # Answer questions
kb add https://github.com/...  # Capture a URL
```

## Features

- **Hybrid Search** — BM25 keyword + vector embeddings + wikilink graph, merged via Reciprocal Rank Fusion
- **Extractive QA** — Ask questions, get answers with source snippets and clickable links
- **Aggregate Queries** — "How many security repos?" answered directly from the index
- **19 MCP Tools** — search, add, create, move, merge, rename, remove, undo, journal, insight, reflect, export, and more
- **Cross-Platform** — Claude Code, Cursor, VS Code, Codex CLI, Gemini CLI via MCP
- **Obsidian Integration** — Graph view, Dataview queries, search modal in command palette
- **Local-First** — Your files, your machine. Works offline with free local models.
- **Data Safety** — Soft delete with 30-day trash, full-state undo, crash-safe snapshots

## Search

Three signals, one ranking:

| Signal | What it finds |
|---|---|
| **BM25** (FTS5) | Exact keyword matches with field weighting |
| **Vector** (fastembed) | Semantically similar content, different terminology |
| **Graph** (wikilinks) | Connected pages that keywords miss |

Install `fastembed` for vector search (optional, 130MB):
```bash
pip install athena-brain[search]
```

## MCP Server

Athena runs as a local MCP server. LLM consoles connect automatically:

```json
// .mcp.json (place in your vault root)
{
  "mcpServers": {
    "athena": {
      "command": "python3",
      "args": ["-m", "athena.server", "/path/to/vault"],
      "env": {"PYTHONPATH": "/path/to/athena/server"}
    }
  }
}
```

## Obsidian Plugin

Search from inside Obsidian: `Cmd+P` > "Athena Search" > type your question.

Two modes:
- **Search** — ranked links
- **Query** — answer snippets + source links

## Architecture

```
LLM Console (Claude Code / Cursor / VS Code)
    │ MCP protocol
    ▼
Athena MCP Server (local process)
    ├── Hybrid search engine (.athena/search.db)
    ├── 19 tool handlers
    └── Obsidian vault (raw/ + wiki/)
```

## Commands

| Command | What it does |
|---|---|
| `kb search <query>` | Hybrid search with ranked results |
| `kb query <question>` | Answer a question from your KB |
| `kb add <url>` | Capture a URL (webpage, repo, paper, video) |
| `kb index` | Build/rebuild search index |
| `kb create <name>` | Create a topic hub page |
| `kb move <page> --into <hub>` | Organize pages into groups |
| `kb merge <p1> <p2>` | Merge pages into one |
| `kb rename <page> --to <new>` | Rename with wikilink update |
| `kb remove <page>` | Soft delete (30-day trash) |
| `kb undo` | Restore last operation |
| `kb journal <text>` | Write a learning note |
| `kb insight <title>` | Save a finding with rules |
| `kb lint` | Health check + auto-fix |
| `kb stats` | Show KB statistics |
| `kb config` | Configure LLM provider |

## Requirements

- Python 3.10+
- Node.js (for HTML-to-markdown conversion)
- Obsidian (optional, for graph view and search UI)
