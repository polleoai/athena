#!/usr/bin/env python3
"""bulk-llm-regen-tags.py — generate tags for wiki pages with empty tags: [].

Targets pages where the tags: field is the literal `[]`. Walks them, asks
Opus via `claude -p` to pick 3-7 tags from the canonical Athena taxonomy,
and rewrites the tags: line.

Usage (from vault root):
  scripts/bulk-llm-regen-tags.py --dry-run --show-output
  scripts/bulk-llm-regen-tags.py
"""
from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path

# Shared idle-timeout helper — see scripts/_athena_timeout.py for rationale.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _athena_timeout import (  # noqa: E402
    extract_text_from_stream_json,
    resolve_timeout_seconds,
    run_claude_with_idle_timeout,
)

VAULT = Path(__file__).resolve().parent.parent
WIKI_FORMAT = VAULT / "wiki" / "format"
MAX_BODY_CHARS = 8000

MODEL = "claude-opus-4-7"

# Canonical taxonomy by frequency (top tags from the existing vault).
# The LLM is told to prefer these but may add adjacent terms when needed.
CANONICAL_TAGS = [
    "llm", "ai-agents", "claude-code", "tool", "security", "ml",
    "course", "webpage", "memory", "python", "deep-learning", "rag",
    "book", "obsidian", "video", "curated-list", "math", "mcp",
    "repo", "tools", "knowledge-management", "skills", "second-brain",
    "prompt-engineering", "productivity", "paper", "finance", "learning",
    "workflow", "ai-engineering", "interview-prep",
]

SYSTEM_PROMPT = f"""You are tagging a page in a personal Obsidian knowledge base. Pick 3-7 tags from the canonical Athena taxonomy below. Stay close to the canonical list — only add a tag outside the list if a clear concept on the page has no canonical equivalent.

CANONICAL TAGS (use these whenever possible):
{', '.join(CANONICAL_TAGS)}

RULES:
1. Pick 3-7 tags total.
2. Lowercase, hyphen-separated (no spaces, no underscores).
3. Prefer canonical tags. Only invent a new tag if necessary.
4. Tags reflect WHAT the page is about, not the source platform — DO NOT pick a tag based purely on the URL host (e.g., don't tag `x-com` just because it's an X post).
5. If the page has a clear source-type label, you MAY add ONE source-type tag from: webpage, video, paper, repo, book, curated-list. Skip if unclear.
6. No marketing tags ("amazing", "useful"). Just topic tags.

OUTPUT FORMAT: Return ONLY a comma-separated list of tags on a single line. No JSON wrapper, no preamble, no explanation, no quotes around individual tags. Example output: ai-agents, llm, memory, claude-code, second-brain"""

FORBIDDEN_TAG_CHARS = re.compile(r"[^a-z0-9-]")


def parse_frontmatter(text: str) -> tuple[dict, int]:
    if not text.startswith("---"):
        return {}, 0
    end_match = re.search(r"\n---[\s]*\n", text[3:])
    if not end_match:
        return {}, 0
    fm_block = text[3 : end_match.start() + 3]
    fm: dict = {}
    cur_key: str | None = None
    for line in fm_block.split("\n"):
        if not line.strip():
            continue
        if line.startswith("  - ") and cur_key is not None:
            fm.setdefault(cur_key, []).append(line[4:].strip().strip('"').strip("'"))
            continue
        m = re.match(r"^([\w_-]+)\s*:\s*(.*)$", line)
        if not m:
            continue
        cur_key = m.group(1).strip()
        val = m.group(2).strip()
        if val:
            fm[cur_key] = val.strip('"').strip("'")
        else:
            fm[cur_key] = []
    return fm, end_match.end() + 3


def get_page_body(text: str) -> str:
    _, fm_end = parse_frontmatter(text)
    body = text[fm_end:].strip()
    if len(body) > MAX_BODY_CHARS:
        body = body[:MAX_BODY_CHARS] + "\n[truncated]"
    return body


def parse_tags(raw: str) -> list[str]:
    """Parse the LLM's comma-separated tag list, normalize, dedupe."""
    out: list[str] = []
    seen: set[str] = set()
    for tok in re.split(r"[,\n]", raw):
        t = tok.strip().strip('"').strip("'").strip("[]").lower()
        if not t:
            continue
        # Normalize spaces/underscores to hyphens, then strip non-allowed chars.
        t = t.replace(" ", "-").replace("_", "-")
        t = FORBIDDEN_TAG_CHARS.sub("", t)
        if t and t not in seen:
            out.append(t)
            seen.add(t)
    return out


def update_tags_field(wiki_path: Path, tags: list[str], dry_run: bool = False) -> bool:
    """Replace `tags: []` with `tags: [a, b, c]`. Targets only the empty-list form."""
    text = wiki_path.read_text(encoding="utf-8")
    new_tags = "[" + ", ".join(tags) + "]"
    new_text, count = re.subn(
        r"^tags:\s*\[\s*\]\s*$",
        f"tags: {new_tags}",
        text,
        count=1,
        flags=re.MULTILINE,
    )
    if count == 0 or new_text == text:
        return False
    if not dry_run:
        wiki_path.write_text(new_text, encoding="utf-8")
    return True


def call_claude(title: str, source_type: str, body: str) -> list[str]:
    user_prompt = (
        f"PAGE TITLE: {title}\n"
        f"SOURCE TYPE: {source_type or 'unknown'}\n\n"
        f"PAGE BODY:\n{body}\n\n"
        f"Output the comma-separated tag list now."
    )
    cmd = [
        "claude", "-p",
        "--disable-slash-commands",
        "--output-format", "stream-json",
        "--verbose",
        "--include-partial-messages",
        "--model", MODEL,
        "--append-system-prompt", SYSTEM_PROMPT,
        user_prompt,
    ]
    idle_s = resolve_timeout_seconds(MODEL, vault=VAULT)
    rc, raw_out, raw_err = run_claude_with_idle_timeout(cmd, idle_s)
    if rc != 0:
        raise RuntimeError(f"claude -p exit {rc}: {raw_err.strip()[:300] or '(empty)'}")
    return parse_tags(extract_text_from_stream_json(raw_out).strip())


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--show-output", action="store_true")
    p.add_argument("--max-retries", type=int, default=2)
    args = p.parse_args()

    # Find candidates (pages with literal `tags: []`).
    candidates = []
    for wp in sorted(WIKI_FORMAT.rglob("*.md")):
        if wp.name in ("_Contents.md", "_TEMPLATE.md"):
            continue
        text = wp.read_text(encoding="utf-8")
        if re.search(r"^tags:\s*\[\s*\]\s*$", text, re.MULTILINE):
            candidates.append(wp)

    print(f"Found {len(candidates)} pages with empty tags: []")
    if not candidates:
        return 0

    stats = {"updated": 0, "failed": 0}
    started = time.time()

    for idx, wp in enumerate(candidates, start=1):
        if args.limit and stats["updated"] >= args.limit:
            break
        rel = wp.relative_to(VAULT)
        text = wp.read_text(encoding="utf-8")
        fm, _ = parse_frontmatter(text)
        title = fm.get("title", wp.stem)
        source_type = fm.get("source_type", "")
        body = get_page_body(text) or "(empty body)"

        print(f"[{idx}/{len(candidates)}] {rel}", flush=True)

        tags: list[str] = []
        last_err = ""
        for attempt in range(args.max_retries + 1):
            try:
                tags = call_claude(title, source_type, body)
            except (TimeoutError, RuntimeError) as exc:
                last_err = str(exc)
                print(f"  call failed (attempt {attempt+1}): {exc}", flush=True)
                tags = []
                continue
            if 3 <= len(tags) <= 8:
                break
            print(f"  bad-count (attempt {attempt+1}): got {len(tags)} tags: {tags}", flush=True)
            tags = []

        if not tags:
            stats["failed"] += 1
            print(f"  GIVE UP: {last_err or 'no valid tags after retries'}", flush=True)
            continue

        print(f"  -> tags: {tags}", flush=True)
        if update_tags_field(wp, tags, dry_run=args.dry_run):
            stats["updated"] += 1

    elapsed = time.time() - started
    print(f"\n=== bulk-llm-regen-tags done in {elapsed:.0f}s ===")
    for k, v in stats.items():
        print(f"  {k}: {v}")
    if args.dry_run:
        print("(dry-run — no files written)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
