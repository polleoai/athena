"""`kb query <question>` -- search + snippet extraction (and aggregate counting).

Ports the `query)` arm of bin/kb-legacy: the shell built QUERY from "$*" and
checked for emptiness, then ran the heredoc body. Both the shell-level arg
handling and the heredoc body are reproduced here verbatim in pure Python.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from typing import List


def handle(argv: List[str], root: str) -> int:
    # QUERY="$*"
    query = " ".join(argv)
    if not query:
        print("Usage: kb query <question>")
        print("  Searches the KB and extracts relevant snippets to answer your question.")
        print("  Configure an LLM for full synthesized answers: kb config set llm ollama")
        return 1

    sys.path.insert(0, os.path.join(root, "bin", "lib"))
    from search import search_or_grep, _parse_frontmatter, _ensure_db, _db_path

    kb_root = root
    query_lower = query.lower()

    # Step 0: Detect aggregate/counting questions and answer from index directly
    is_aggregate = bool(re.search(
        r"how many|count|list all|total number|do i have|have we collected",
        query_lower))

    type_map = {
        "repo": ["repo", "repos", "repository", "repositories", "github"],
        "webpage": ["webpage", "webpages", "article", "articles", "blog", "blogs", "website"],
        "paper": ["paper", "papers", "arxiv", "research paper"],
        "video": ["video", "videos", "youtube", "course", "courses", "lecture"],
        "topic": ["topic", "topics", "hub", "hubs"],
        "entity": ["entity", "entities", "person", "people", "org"],
        "image": ["image", "images", "screenshot", "diagram"],
        "insight": ["insight", "insights", "finding"],
    }
    detected_type = None
    for stype, keywords in type_map.items():
        if any(kw in query_lower for kw in keywords):
            detected_type = stype
            break

    if is_aggregate and os.path.exists(_db_path(kb_root)):
        conn = _ensure_db(kb_root)

        if detected_type:
            filter_words = set()
            all_stop = set(type_map.get(detected_type, []))
            all_stop.update(["how", "many", "do", "i", "have", "we", "collected",
                             "are", "is", "in", "our", "the", "a", "an", "of",
                             "related", "about", "that", "which", "list", "all",
                             "count", "total", "number", "show", "me", "give",
                             "find", "what", "repo", "repos", "github", "page", "pages"])
            for word in re.sub(r"[^\w\s]", " ", query_lower).split():
                if word and len(word) > 1 and word not in all_stop:
                    filter_words.add(word)

            if filter_words:
                all_pages = conn.execute(
                    "SELECT page_id, title, rel_path, summary FROM pages WHERE source_type = ? ORDER BY title",
                    (detected_type,)).fetchall()
                pages = []
                for pid, title, rel_path, summary in all_pages:
                    text = (title + " " + (summary or "")).lower()
                    if any(fw in text for fw in filter_words):
                        pages.append((title, rel_path))
                count = len(pages)

                if count == 0:
                    all_pages = conn.execute(
                        "SELECT page_id, title, rel_path, summary, source_type FROM pages ORDER BY title"
                    ).fetchall()
                    pages = []
                    for pid, title, rel_path, summary, stype in all_pages:
                        text = (title + " " + (summary or "")).lower()
                        if any(fw in text for fw in filter_words):
                            pages.append((title, rel_path))
                    count = len(pages)
                    if count > 0:
                        detected_type = "page"
            else:
                count = conn.execute("SELECT COUNT(*) FROM pages WHERE source_type = ?",
                                     (detected_type,)).fetchone()[0]
                pages = conn.execute(
                    "SELECT title, rel_path FROM pages WHERE source_type = ? ORDER BY title",
                    (detected_type,)).fetchall()

            print(f'Query: "{query}"\n')
            type_label = detected_type if detected_type != "page" else ""
            filter_label = ' matching "' + " ".join(filter_words) + '"' if filter_words else ""
            print(f'  You have {count} {type_label} page{"s" if count != 1 else ""}{filter_label} in your knowledge base.\n')
            for i, (title, rel_path) in enumerate(pages, 1):
                print(f"  {i}. [[{title}]]")

            query_page = os.path.join(kb_root, "wiki", "dashboards", "Search Results.md")
            now = time.strftime("%Y-%m-%d %H:%M")
            lines = [
                "---", 'title: "Search Results"', 'source_type: "topic"',
                "tags: [dashboard]", f'last_updated: {time.strftime("%Y-%m-%d")}',
                "---", "",
                f'# Query: "{query}"', "",
                f"> {now} · aggregate query", "",
                "## Answer", "",
                f'You have **{count}** {type_label} page{"s" if count != 1 else ""}{filter_label} in your knowledge base.', "",
            ]
            for i, (title, _) in enumerate(pages, 1):
                lines.append(f"{i}. [[{title}]]")
            lines.append("")
            os.makedirs(os.path.dirname(query_page), exist_ok=True)
            with open(query_page, "w") as f:
                f.write("\n".join(lines))
            print("\n  Written to: wiki/dashboards/Search Results.md")
        else:
            rows = conn.execute(
                "SELECT source_type, COUNT(*) FROM pages GROUP BY source_type ORDER BY COUNT(*) DESC"
            ).fetchall()
            total = sum(c for _, c in rows)

            print(f'Query: "{query}"\n')
            print(f"  You have {total} pages in your knowledge base:\n")
            for stype, count in rows:
                print(f"    {stype}: {count}")

            query_page = os.path.join(kb_root, "wiki", "dashboards", "Search Results.md")
            now = time.strftime("%Y-%m-%d %H:%M")
            lines = [
                "---", 'title: "Search Results"', 'source_type: "topic"',
                "tags: [dashboard]", f'last_updated: {time.strftime("%Y-%m-%d")}',
                "---", "",
                f'# Query: "{query}"', "",
                f"> {now} · aggregate query", "",
                "## Answer", "",
                f"You have **{total} pages** in your knowledge base:", "",
                "| Type | Count |", "|---|---|",
            ]
            for stype, count in rows:
                lines.append(f"| {stype} | {count} |")
            lines.append("")
            os.makedirs(os.path.dirname(query_page), exist_ok=True)
            with open(query_page, "w") as f:
                f.write("\n".join(lines))
            print("\n  Written to: wiki/dashboards/Search Results.md")

        conn.close()
        return 0

    # Step 1: Search (non-aggregate questions)
    ok, results = search_or_grep(kb_root, query, top_k=5)
    if not results:
        print(f"No results for: {query}")
        return 0

    # Step 2: Read top pages and extract relevant snippets
    snippets = []
    sources = []
    for r in results[:5]:
        rel_path = r.get("rel_path", "")
        filepath = os.path.join(kb_root, rel_path)
        if not os.path.exists(filepath):
            continue

        meta, body = _parse_frontmatter(filepath)
        title = r["title"]
        source_type = r.get("source_type", "")
        sources.append({"title": title, "source_type": source_type, "rel_path": rel_path})

        sections = re.split(r"^(#{2,3}\s+.+)$", body, flags=re.MULTILINE)

        query_terms = [t.lower() for t in re.sub(r"[^\w\s]", " ", query).split()
                       if len(t) > 2]

        summary = meta.get("summary", "")
        if summary:
            summary_score = sum(1 for t in query_terms if t in summary.lower())
            if summary_score >= 2 or (summary_score >= 1 and len(query_terms) <= 2):
                snippets.append({
                    "title": title,
                    "heading": None,
                    "text": summary,
                    "score": summary_score + 1,
                })

        current_heading = None
        for part in sections:
            heading_match = re.match(r"^#{2,3}\s+(.+)$", part.strip())
            if heading_match:
                current_heading = heading_match.group(1).strip()
                continue

            text = part.strip()
            if not text or len(text) < 30:
                continue

            text_lower = text.lower()
            term_hits = sum(1 for t in query_terms if t in text_lower)
            if term_hits == 0:
                continue

            has_table = "|" in text and "---" in text
            table_bonus = 2 if has_table else 0

            if has_table:
                trunc_lines = text.split("\n")[:17]
                trunc_text = "\n".join(trunc_lines)
            else:
                trunc_text = text[:500]

            snippets.append({
                "title": title,
                "heading": current_heading,
                "text": trunc_text,
                "score": term_hits + table_bonus,
            })

    # Step 3: Rank snippets and deduplicate
    snippets.sort(key=lambda s: s["score"], reverse=True)
    seen_titles = set()
    top_snippets = []
    for s in snippets:
        key = (s["title"], s.get("heading", ""))
        if key not in seen_titles and len(top_snippets) < 5:
            seen_titles.add(key)
            top_snippets.append(s)

    # Step 4: Print to terminal
    print(f'Query: "{query}"\n')

    if top_snippets:
        for s in top_snippets:
            src = f"[[{s['title']}]]"
            if s.get("heading"):
                src += f" > {s['heading']}"
            print(f"  {src}")

            text = s["text"].strip()
            if len(text) > 300:
                text = text[:297] + "..."
            for line in text.split("\n"):
                print(f"    {line}")
            print()
    else:
        print("  No relevant snippets found. Try a more specific query.\n")

    print("Sources:")
    for i, s in enumerate(sources, 1):
        print(f"  {i}. [[{s['title']}]] [{s['source_type']}]")

    # Step 5: Write to Obsidian
    query_page = os.path.join(kb_root, "wiki", "dashboards", "Search Results.md")
    now = time.strftime("%Y-%m-%d %H:%M")

    lines = [
        "---",
        'title: "Search Results"',
        'source_type: "topic"',
        "tags: [dashboard]",
        f'last_updated: {time.strftime("%Y-%m-%d")}',
        "---",
        "",
        f'# Query: "{query}"',
        "",
        f"> {now} · {len(top_snippets)} snippets from {len(sources)} sources",
        "",
    ]

    if top_snippets:
        lines.append("## Answer")
        lines.append("")
        for s in top_snippets:
            src = f"[[{s['title']}]]"
            if s.get("heading"):
                lines.append(f'**From {src} > {s["heading"]}:**')
            else:
                lines.append(f"**From {src}:**")
            lines.append("")
            snippet_text = s["text"].strip()
            has_table = "|" in snippet_text and "---" in snippet_text
            if has_table:
                table_lines = []
                for text_line in snippet_text.split("\n"):
                    table_lines.append(text_line)
                    if table_lines and not text_line.strip().startswith("|") and len(table_lines) > 3:
                        break
                lines.extend(table_lines)
            else:
                for text_line in snippet_text.split("\n"):
                    lines.append(f"> {text_line}")
            lines.append("")

    lines.append("## Sources")
    lines.append("")
    for i, s in enumerate(sources, 1):
        lines.append(f'{i}. [[{s["title"]}]] *{s["source_type"]}*')
    lines.append("")

    config_path = os.path.join(kb_root, ".athena", "config.json")
    has_llm = False
    if os.path.exists(config_path):
        try:
            with open(config_path) as f:
                cfg = json.load(f)
            has_llm = bool(cfg.get("llm", {}).get("provider"))
        except (json.JSONDecodeError, IOError):
            pass

    if not has_llm:
        lines.append("---")
        lines.append("*For full synthesized answers, configure an LLM: `kb config set llm ollama`*")
        lines.append("")

    os.makedirs(os.path.dirname(query_page), exist_ok=True)
    with open(query_page, "w") as f:
        f.write("\n".join(lines))

    print("\n  Written to: wiki/dashboards/Search Results.md")
    return 0
