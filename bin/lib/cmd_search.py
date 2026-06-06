"""`kb search <topic>` -- hybrid (or grep-fallback) search.

Ports the `search)` arm of bin/kb-legacy: the shell built QUERY from "$*" and
checked for emptiness, then ran the heredoc body. Both the shell-level arg
handling and the heredoc body are reproduced here in pure Python so behavior is
identical on every platform.
"""
from __future__ import annotations

import os
import sys
import time
from typing import List


def handle(argv: List[str], root: str) -> int:
    # QUERY="$*"  -- join all args with a single space.
    query = " ".join(argv)
    if not query:
        print("Usage: kb search <topic>")
        return 1

    sys.path.insert(0, os.path.join(root, "bin", "lib"))
    from search import search_or_grep

    kb_root = root

    ok, results = search_or_grep(kb_root, query, top_k=25)

    if not results:
        print(f"No results for: {query}")
        print("Tip: run 'kb index' to build the search index for ranked results.")
        return 0

    # Check if using grep fallback
    using_grep = results and results[0].get("signals") == ["grep"]
    if using_grep:
        print(f"Grep results for: {query}")
        print("Tip: run 'kb index' to enable ranked hybrid search.\n")
    else:
        print(f"Search results for: {query}\n")

    # Print to terminal
    for i, r in enumerate(results, 1):
        signals = ", ".join(r.get("signals", []))
        print(f"  {i}. {r['title']}")
        if r.get("summary"):
            summary = r["summary"][:100]
            if len(r.get("summary", "")) > 100:
                summary += "..."
            print(f"     {summary}")
        parts = []
        if r.get("source_type"):
            parts.append(r["source_type"])
        if signals and not using_grep:
            parts.append(signals)
        if r.get("score") and not using_grep:
            parts.append(f"score={r['score']}")
        if parts:
            print(f"     [{'] ['.join(parts)}]")
        print()

    # Write results to Obsidian-rendered wiki page
    search_page = os.path.join(kb_root, "wiki", "dashboards", "Search Results.md")
    now = time.strftime("%Y-%m-%d %H:%M")
    mode = "grep" if using_grep else "hybrid (BM25 + vector + graph)"

    lines = [
        "---",
        'title: "Search Results"',
        'source_type: "topic"',
        "tags: [dashboard]",
        f'last_updated: {time.strftime("%Y-%m-%d")}',
        "---",
        "",
        f'# Search: "{query}"',
        "",
        f"> {len(results)} results · {mode} · {now}",
        "",
    ]

    for i, r in enumerate(results, 1):
        title = r["title"]
        signals = ", ".join(r.get("signals", []))
        source_type = r.get("source_type", "")
        score = r.get("score", 0)
        summary = r.get("summary", "")

        lines.append(f"### {i}. [[{title}]]")
        if summary:
            lines.append(f"> {summary}")
        tag_parts = []
        if source_type:
            tag_parts.append(f"`{source_type}`")
        if signals and not using_grep:
            tag_parts.append(f"signals: {signals}")
        if score and not using_grep:
            tag_parts.append(f"score: {score}")
        if tag_parts:
            lines.append(f'*{" · ".join(tag_parts)}*')
        lines.append("")

    os.makedirs(os.path.dirname(search_page), exist_ok=True)
    with open(search_page, "w") as f:
        f.write("\n".join(lines))

    print("  Results written to: wiki/dashboards/Search Results.md")
    print("  Open in Obsidian to see clickable links and graph connections.")
    return 0
