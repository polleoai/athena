#!/usr/bin/env python3
"""bulk-llm-regen-summaries.py — LLM-driven structured digest regeneration for the Athena wiki.

Walks wiki/format/* and replaces each page with:
  - A fresh one-paragraph `summary:` in frontmatter (250-400 chars)
  - Structured body digest sections:
      ## Key Findings   — 3-7 concrete bullets
      ## Methods / Architecture   — for papers/repos/technical posts (omitted for opinion/social)
      ## Notable Quotes   — 1-3 verbatim snippets (omitted if none)
      ## Relevance   — why it matters in the KB

The raw body (source text, images) stays in raw/ and is reachable via Local Copy.
The wiki page becomes a synthesis artifact, not a copy.

Driver is `claude -p` (Claude Code CLI, subscription-billed) running Opus 4.7
with --bare and stream-json output.

Usage (from vault root):
  scripts/bulk-llm-regen-summaries.py --dry-run --limit 3
  scripts/bulk-llm-regen-summaries.py --only-paths sample.txt
  scripts/bulk-llm-regen-summaries.py             # full run, all pages

Resumable: completed pages logged to scripts/.bulk-regen-state.json. Re-runs
skip already-completed pages unless --reset-state is passed.

Why CLI not API: user has Claude Code subscription; CLI invocation is
subscription-billed (free at usage tier) instead of per-token.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
# `bin/lib/` carries shared helpers (preprocess_content), needed below for
# per-source-type chrome stripping so YouTube comment trees / LinkedIn
# sidebar junk don't bleed into the LLM-generated summary. Without this,
# the synthesis-after-create path bypasses the in-memory clean that
# wiki_page.create_wiki_page applies on direct ingest.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "bin" / "lib"))
from wiki_page import preprocess_content  # noqa: E402
from _athena_timeout import (  # noqa: E402
    extract_text_from_stream_json,
    resolve_timeout_seconds,
    run_claude_with_idle_timeout,
)

VAULT = Path(__file__).resolve().parent.parent
WIKI_FORMAT = VAULT / "wiki" / "format"
STATE_FILE = VAULT / "scripts" / ".bulk-regen-state.json"
MAX_BODY_CHARS = 12000  # truncate body sent to LLM (token budget)
MIN_SUMMARY = 100
MAX_SUMMARY = 600

MODEL = "claude-opus-4-7"

DIGEST_SYSTEM_PROMPT = """You are writing a structured digest for a page in a personal knowledge base (Obsidian vault). The KB stores technical content: AI papers, security research, blog posts, LinkedIn/X posts, GitHub repos, videos, and conference talks. The user is a practitioner who needs to quickly recall what each page contains and why it matters.

You will receive the page title, source type, and body text. Produce a JSON object with exactly two keys:

"summary": One prose paragraph for the frontmatter summary field. Rules:
  - Front-load the WHAT in the first 5-15 words. State directly what the page is.
  - NEVER start with "This article describes", "This page is about", "An overview of", "A guide to", "This document explains", "The article discusses", or similar filler.
  - Name 2-4 concrete distinguishing details: specific numbers, named techniques, named entities, key claims.
  - If the body is sparse (only a title + URL, or only HTML chrome with no meaningful content), say so honestly: "Sparse capture — only the title and URL are stored; see source for content."
  - NEVER infer or invent author names, organization affiliations, or proper nouns not in the source. If the body doesn't name the author, write "the author" or "the post".
  - Plain prose. No headers, bullets, or line breaks. No markdown other than backticks for code identifiers.
  - Length: target 250-400 characters; HARD CEILING 550 characters.
  - Forbidden marketing words: powerful, cutting-edge, innovative, robust, seamless, comprehensive, leverages, unleashes, revolutionary, game-changing, state-of-the-art, world-class, next-generation.

"body": Structured markdown digest with the following sections (use exactly these headers):

## Key Findings

3 to 7 bullet points. Each bullet is one concrete, specific takeaway the user can recall months later. Include numbers, named methods, named entities, specific claims. Avoid vague generalities like "the author discusses X" or "covers important topics". If the source is sparse or thin, say so in 1-2 bullets.

## Methods / Architecture

INCLUDE ONLY for: technical papers, repos, deep-dive blog posts about systems or algorithms, security research with specific techniques. OMIT for: LinkedIn opinion posts, news articles, videos without technical detail, social media posts, conference agendas, sparse captures.

If included: 2-5 sentences describing the system design, algorithm, or methodology. Be specific — name the components, data structures, or steps.

## Notable Quotes

INCLUDE ONLY if the source contains memorable, quotable text (a sharp insight, a striking claim, a useful definition). OMIT if no strong quotes exist — do NOT invent quotes.

If included: 1-3 blockquotes using > syntax. Copy verbatim from the body text.

## Relevance

1-3 sentences. Why this page matters in the context of AI engineering, security research, knowledge management, or whatever domain it belongs to. What would the user do with this information?

---

SECTION RULES:
- Include all four sections by default.
- Omit "Methods / Architecture" for non-technical content.
- Omit "Notable Quotes" if no strong quotes exist.
- Never fabricate content. If a section would be empty or invented, omit it.
- Use standard markdown. No HTML.

OUTPUT FORMAT: Return ONLY a JSON object on a single line. No preamble, no explanation, no markdown code fences. Example shape:
{"summary": "...", "body": "## Key Findings\\n\\n- ...\\n\\n## Relevance\\n\\n..."}"""

FORBIDDEN_OPENERS = (
    "this article",
    "this page",
    "this document",
    "this repository",
    "this repo",
    "this paper",
    "this video",
    "an overview of",
    "a guide to",
    "the article",
    "the page",
    "the document",
)

FORBIDDEN_WORDS = (
    "cutting-edge",
    "state-of-the-art",
    "world-class",
    "next-generation",
    "game-changing",
    "revolutionary",
)


def parse_frontmatter(text: str) -> tuple[dict, int]:
    """Return (fm_dict, fm_end_offset)."""
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
    """Return body content after frontmatter, truncated to MAX_BODY_CHARS."""
    _, fm_end = parse_frontmatter(text)
    body = text[fm_end:].strip()
    if len(body) > MAX_BODY_CHARS:
        body = body[:MAX_BODY_CHARS] + "\n\n[... truncated for token budget ...]"
    return body


def get_raw_body(wiki_path: Path, fm: dict) -> str:
    """Return the raw source body for a wiki page (better than the wiki body for synthesis).

    Prefers the raw file (raw_path frontmatter key) since that has the full
    untruncated content. Falls back to the wiki body if raw is unavailable.
    """
    raw_path_rel = fm.get("raw_path", "")
    if raw_path_rel:
        raw_abs = VAULT / raw_path_rel
        if raw_abs.is_file():
            try:
                raw_text = raw_abs.read_text(encoding="utf-8", errors="replace")
                # Delegate frontmatter-stripping + per-source-type chrome
                # cleaning (LinkedIn sidebar, YouTube comment trees, social
                # UI artifacts) to the shared helper so this synthesis
                # path applies the SAME cleaners as wiki_page.create_wiki_page.
                # Otherwise the synthesis-after-create flow re-introduces
                # the noise we strip on ingest — witnessed 2026-05-31 with
                # a Jason Lee YouTube wiki whose summary quoted commenters
                # despite the on-ingest comment-strip being in place.
                parsed = preprocess_content(raw_text)
                body = parsed["body"]
                if len(body) > MAX_BODY_CHARS:
                    body = body[:MAX_BODY_CHARS] + "\n\n[... truncated for token budget ...]"
                return body
            except (IOError, OSError):
                pass
    # Fallback: use wiki page body
    text = wiki_path.read_text(encoding="utf-8")
    return get_page_body(text)


def _replace_summary_in_text(text: str, new_summary: str) -> str:
    """Replace summary: field in frontmatter."""
    safe = new_summary.replace("\\", "\\\\").replace('"', '\\"')
    new_text, count = re.subn(
        r'^summary:\s*"[^"\n]*"\s*$',
        f'summary: "{safe}"',
        text, count=1, flags=re.MULTILINE,
    )
    if count == 0:
        new_text, count = re.subn(
            r"^summary:\s*[^\n]*$",
            f'summary: "{safe}"',
            text, count=1, flags=re.MULTILINE,
        )
    if count == 0:
        if text.startswith("---"):
            end_match = re.search(r"\n---[\s]*\n", text[3:])
            if end_match:
                insert_at = end_match.start() + 3
                prefix = "\n" if (insert_at > 0 and text[insert_at - 1] != "\n") else ""
                new_text = (
                    text[:insert_at]
                    + prefix
                    + f'summary: "{safe}"\n'
                    + text[insert_at:]
                )
    return new_text


def update_wiki_digest(
    wiki_path: Path, new_summary: str, new_body: str, dry_run: bool = False
) -> bool:
    """Replace both the summary frontmatter field and the digest body sections.

    The "body zone" is everything after the Source/Local Copy line and before
    the ## Connections or ## Keywords sections. This zone is replaced wholesale
    with the new structured digest markdown.

    Returns True if the file was (or would be) changed.
    """
    text = wiki_path.read_text(encoding="utf-8")

    # Update summary in frontmatter
    new_text = _replace_summary_in_text(text, new_summary)

    # Find frontmatter end
    if not new_text.startswith("---"):
        return False
    end_match = re.search(r"\n---[\s]*\n", new_text[3:])
    if not end_match:
        return False
    fm_end = end_match.end() + 3

    body_zone = new_text[fm_end:]
    lines = body_zone.split('\n')

    # Find source line index: first line containing "Local Copy" (the canonical
    # Source · [[Local Copy]] line that build_wiki_page always emits)
    source_end_idx = None
    for i, line in enumerate(lines):
        if 'Local Copy' in line:
            source_end_idx = i
            break
    # Fallback: first non-empty line after frontmatter if Local Copy not found
    if source_end_idx is None:
        for i, line in enumerate(lines):
            if line.strip():
                source_end_idx = i
                break
    if source_end_idx is None:
        return False

    # Find where Connections/Keywords sections begin
    sections_start_idx = None
    for i, line in enumerate(lines):
        if line.startswith('## Connections') or line.startswith('## Keywords'):
            sections_start_idx = i
            break

    prefix_lines = lines[:source_end_idx + 1]
    suffix_lines = lines[sections_start_idx:] if sections_start_idx is not None else []

    new_body_zone = (
        '\n'.join(prefix_lines)
        + '\n\n'
        + new_body.strip()
        + '\n\n'
        + ('\n'.join(suffix_lines) if suffix_lines else '')
    ).rstrip() + '\n'

    result = new_text[:fm_end] + new_body_zone

    if result == text:
        return False
    if not dry_run:
        wiki_path.write_text(result, encoding='utf-8')
    return True


def quality_check_summary(summary: str) -> tuple[bool, str]:
    """Return (ok, reason). reason is empty when ok."""
    if not summary or not summary.strip():
        return False, "empty"
    s = summary.strip()
    low = s.lower()
    if len(s) < MIN_SUMMARY and "sparse capture" not in low:
        return False, f"too-short ({len(s)} chars, no sparse-capture admission)"
    if len(s) > MAX_SUMMARY:
        return False, f"too-long ({len(s)} chars)"
    for opener in FORBIDDEN_OPENERS:
        if low.startswith(opener):
            return False, f"forbidden-opener ({opener!r})"
    for word in FORBIDDEN_WORDS:
        if word in low:
            return False, f"forbidden-word ({word!r})"
    if "\n" in s:
        return False, "contains-newline"
    for ch in s:
        cp = ord(ch)
        if 0x1D400 <= cp <= 0x1D7FF:
            return False, f"unicode-math-bold-leak (char {hex(cp)!r})"
    return True, ""


def quality_check_body(body: str) -> tuple[bool, str]:
    """Validate structured digest body."""
    if not body or not body.strip():
        return False, "body-empty"
    if "## Key Findings" not in body:
        return False, "missing-key-findings"
    # Key Findings must have at least one bullet
    kf_match = re.search(r'## Key Findings\n+(.*?)(?=\n## |\Z)', body, re.DOTALL)
    if kf_match:
        kf_content = kf_match.group(1).strip()
        if not kf_content or kf_content.startswith('*Pending'):
            return False, "key-findings-empty"
    return True, ""


def call_claude_for_digest(
    title: str, source_type: str, existing_summary: str, body: str
) -> dict:
    """Invoke `claude -p` and return { summary, body } dict.

    Raises RuntimeError on subprocess failure.
    Returns empty dict on parse failure (caller decides whether to retry).
    """
    user_prompt = (
        f"PAGE TITLE: {title}\n"
        f"SOURCE TYPE: {source_type or 'unknown'}\n"
        f"EXISTING SUMMARY (may be low-quality, use for context only):\n{existing_summary or '(none)'}\n\n"
        f"PAGE BODY (source content):\n{body}\n\n"
        f"Generate the JSON digest now."
    )
    cmd = [
        "claude",
        "-p",
        "--disable-slash-commands",
        "--output-format", "stream-json",
        "--verbose",
        "--include-partial-messages",
        "--model", MODEL,
        "--append-system-prompt", DIGEST_SYSTEM_PROMPT,
        user_prompt,
    ]
    idle_s = resolve_timeout_seconds(MODEL, vault=VAULT)
    rc, raw_out, raw_err = run_claude_with_idle_timeout(cmd, idle_s)
    if rc != 0:
        raise RuntimeError(
            f"claude -p exit {rc}: stderr={raw_err.strip()[:500] or '(empty)'}"
        )
    out = extract_text_from_stream_json(raw_out).strip()

    # Strip markdown code fences if model wrapped output
    out = re.sub(r'^```(?:json)?\s*', '', out, flags=re.MULTILINE)
    out = re.sub(r'\s*```\s*$', '', out, flags=re.MULTILINE)
    out = out.strip()

    # Parse JSON
    try:
        result = json.loads(out)
    except json.JSONDecodeError:
        # Try to find JSON object in output
        m = re.search(r'\{.*\}', out, re.DOTALL)
        if m:
            try:
                result = json.loads(m.group())
            except json.JSONDecodeError:
                return {}
        else:
            return {}

    if not isinstance(result, dict):
        return {}

    # Normalize fields
    summary = result.get("summary", "").strip()
    body_md = result.get("body", "").strip()

    # Collapse accidental newlines in summary
    summary = re.sub(r"\s*\n\s*", " ", summary).strip()
    # Strip surrounding quotes from summary if present
    if (summary.startswith('"') and summary.endswith('"')) or \
       (summary.startswith("'") and summary.endswith("'")):
        summary = summary[1:-1].strip()
    # Strip a "Summary:" preface if it slipped through
    summary = re.sub(r"^(Summary|SUMMARY)\s*[:\-]\s*", "", summary)

    return {"summary": summary, "body": body_md}


def load_state() -> dict:
    if STATE_FILE.is_file():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {"completed": [], "started": None}
    return {"completed": [], "started": None}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dry-run", action="store_true",
                   help="Generate digest but don't write files")
    p.add_argument("--limit", type=int, default=0,
                   help="Stop after processing N pages (0 = all)")
    p.add_argument("--only-paths", type=Path,
                   help="File with one wiki path per line; process only those")
    p.add_argument("--reset-state", action="store_true",
                   help="Clear state file before run (re-process completed pages)")
    p.add_argument("--show-output", action="store_true",
                   help="Print each generated summary and body to stdout")
    p.add_argument("--max-retries", type=int, default=2,
                   help="Retry on quality-check failure (default: 2)")
    p.add_argument("--only-newer-than-mins", type=int, default=0,
                   help="Only process pages whose mtime is newer than N "
                        "minutes ago (default: 0 = no filter).")
    args = p.parse_args()

    if not WIKI_FORMAT.is_dir():
        print(f"Vault not found at {WIKI_FORMAT}", file=sys.stderr)
        return 1

    state = {"completed": [], "started": None} if args.reset_state else load_state()
    if state.get("started") is None:
        state["started"] = datetime.now().isoformat(timespec="seconds")
        if not args.dry_run:
            save_state(state)
    completed = set(state.get("completed", []))

    if args.only_paths:
        paths = [Path(line.strip()) for line in args.only_paths.read_text().splitlines() if line.strip()]
        paths = [VAULT / pp if not pp.is_absolute() else pp for pp in paths]
    else:
        paths = sorted(WIKI_FORMAT.rglob("*.md"))
    paths = [pp for pp in paths if pp.name not in ("_Contents.md", "_TEMPLATE.md")]
    if args.only_newer_than_mins > 0:
        cutoff = time.time() - (args.only_newer_than_mins * 60)
        paths = [pp for pp in paths if pp.stat().st_mtime >= cutoff]

    stats = {"updated": 0, "skipped_done": 0, "skipped_no_fm": 0,
             "skipped_redirect": 0, "failed": 0}
    started = time.time()

    for idx, wiki_path in enumerate(paths, start=1):
        if args.limit and stats["updated"] >= args.limit:
            break
        rel = str(wiki_path.relative_to(VAULT))
        if rel in completed:
            stats["skipped_done"] += 1
            continue
        text = wiki_path.read_text(encoding="utf-8")
        fm, _ = parse_frontmatter(text)
        if not fm:
            stats["skipped_no_fm"] += 1
            continue
        if str(fm.get("redirect", "")).lower() == "true":
            stats["skipped_redirect"] += 1
            continue

        title = fm.get("title", wiki_path.stem)
        source_type = fm.get("source_type", "")
        existing = fm.get("summary", "") or ""
        body = get_raw_body(wiki_path, fm)
        if not body:
            body = "(empty body)"

        print(f"[{idx}/{len(paths)}] {rel}", flush=True)

        result: dict = {}
        last_reason = ""
        for attempt in range(args.max_retries + 1):
            try:
                result = call_claude_for_digest(title, source_type, existing, body)
            except (TimeoutError, RuntimeError) as exc:
                print(f"  call failed (attempt {attempt+1}): {exc}", flush=True)
                result = {}
                last_reason = str(exc)
                continue

            if not result:
                last_reason = "empty-json"
                print(f"  quality-fail (attempt {attempt+1}): empty-json", flush=True)
                continue

            summary_ok, summary_reason = quality_check_summary(result.get("summary", ""))
            body_ok, body_reason = quality_check_body(result.get("body", ""))

            if summary_ok and body_ok:
                break
            last_reason = summary_reason or body_reason
            print(f"  quality-fail (attempt {attempt+1}): {last_reason}", flush=True)
            result = {}

        if not result:
            stats["failed"] += 1
            print(f"  GIVE UP after {args.max_retries+1} attempts: {last_reason}", flush=True)
            continue

        new_summary = result["summary"]
        new_body = result["body"]

        if args.show_output:
            print(f"  summary -> {new_summary}", flush=True)
            print(f"  body -> {new_body[:200]}{'...' if len(new_body)>200 else ''}", flush=True)
        else:
            print(f"  summary ({len(new_summary)}c): {new_summary[:100]}{'...' if len(new_summary)>100 else ''}",
                  flush=True)
            print(f"  body ({len(new_body)}c): {new_body[:80].replace(chr(10),' ')}...",
                  flush=True)

        changed = update_wiki_digest(wiki_path, new_summary, new_body, dry_run=args.dry_run)
        if changed:
            stats["updated"] += 1
            if not args.dry_run:
                completed.add(rel)
                state["completed"] = sorted(completed)
                save_state(state)

    elapsed = time.time() - started
    print()
    print(f"=== bulk-llm-regen done in {elapsed:.0f}s ===")
    for k, v in stats.items():
        print(f"  {k}: {v}")
    if args.dry_run:
        print("(dry-run — no files written, state not updated)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
