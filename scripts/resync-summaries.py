#!/usr/bin/env python3
"""resync-summaries.py — one-time re-extraction of wiki page summaries.

Background: wiki_page.py:609 used to cap clean_summary at [:200] chars,
producing truncated dashboard rows like '...with about three hour'. The
cap was bumped to 500 in d6826f1f5, but EXISTING wiki pages keep their
200-char summaries until re-synth'd.

This script walks wiki/format/* and:
  1. Reads each wiki page's frontmatter.
  2. Skips redirect stubs, _Contents.md, and pages without a raw_path.
  3. Reads the corresponding raw content from raw/.../<slug>.md.
  4. Re-extracts a fresh summary up to 500 chars.
  5. Updates ONLY the summary: field in frontmatter (everything else
     untouched — body, tags, related, raw_path, etc.).

Idempotent: pages whose existing summary is already long (>=400 chars)
or whose new summary would be the same/shorter are skipped.

Run from vault root:
  python3 scripts/resync-summaries.py [--dry-run] [--limit N]
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

VAULT = Path(__file__).resolve().parent.parent
WIKI_FORMAT = VAULT / "wiki" / "format"
MAX_SUMMARY = 1500
TRUNCATION_MARKERS = ("…", "...")


def smart_truncate(text: str, max_chars: int = MAX_SUMMARY) -> str:
    """Mirror wiki_page.py:609 logic — sentence-boundary cut at max_chars."""
    if not text or len(text) <= max_chars:
        return text
    head = text[:max_chars]
    sentence_cut = max((head.rfind(p) for p in ('. ', '! ', '? ')), default=-1)
    if sentence_cut > max_chars * 0.66:
        return head[: sentence_cut + 1].rstrip() + ' …'
    word_cut = head.rfind(' ')
    return (head[:word_cut] if word_cut > max_chars * 0.86 else head) + '…'


def parse_frontmatter(text: str) -> tuple[dict, int, int]:
    """Return (fm_dict, fm_start, fm_end). fm_end points past the closing ---."""
    if not text.startswith("---"):
        return {}, 0, 0
    end_match = re.search(r"\n---[\s]*\n", text[3:])
    if not end_match:
        return {}, 0, 0
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
    return fm, 0, end_match.end() + 3


def extract_summary_from_wiki_body(wiki_text: str) -> str:
    """Extract summary from a wiki page's BODY (post-frontmatter content).
    Often higher-quality than the raw because the body was LLM-curated
    when the page was first synth'd. Use this as the primary source for
    re-synth; fall back to raw extraction if body is empty or thin.

    Skips the auto-generated Source line ('![[favicon]] [Source](url) ·
    [[raw|Local Copy]]') and any leading H1, then runs the same
    paragraph-quality filters as raw extraction.
    """
    if not wiki_text.startswith("---"):
        return ""
    fm_end = re.search(r"\n---[\s]*\n", wiki_text[3:])
    if not fm_end:
        return ""
    body = wiki_text[fm_end.end() + 3:]
    # Strip the canonical Source line: '![[favicons/x.png|16]] [Source](url) · [[raw|Local Copy]]'
    body = re.sub(r"^!?\[\[[^\]]+\]\]\s*\[Source\][^\n]*\n", "", body, count=1, flags=re.MULTILINE)
    body = re.sub(r"^\[Source\][^\n]*\n", "", body, count=1, flags=re.MULTILINE)
    # Emoji-prefix Source line: '🌐 [Source](url) · [[raw|Local Copy]]'
    # (BloodHound and other web captures use 🌐 instead of favicon embed)
    body = re.sub(r"^[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F900-\U0001F9FF]+\s*\[Source\][^\n]*\n",
                  "", body, count=1, flags=re.MULTILINE)
    # Generic 'Source · Local Copy' or 'Source · text' first line
    body = re.sub(r"^[^a-zA-Z0-9\n]*Source[^\n]*Local Copy[^\n]*\n", "", body, count=1, flags=re.MULTILINE)
    # '**Source:** <url>' first-paragraph (legacy kb-capture)
    body = re.sub(r"^\*\*Source:\*\*[^\n]*\n", "", body, count=1, flags=re.MULTILINE)
    return _extract_paragraph(body)


def _extract_paragraph(body: str) -> str:
    """Shared paragraph-extraction logic — pick the first substantive
    prose paragraph, skipping headers/bullets/links/UI-noise/TOC.
    Two-pass: prefer >=50-char paragraphs; fall back to >=30 for terse
    descriptions like 'Explanations to key concepts in ML'."""
    body = re.sub(r"^#\s+.*$", "", body, count=1, flags=re.MULTILINE)
    body = re.sub(r"^-\s+\*\*[^*]+\*\*[^\n]*\n", "", body, flags=re.MULTILINE)
    body = re.sub(r"^#+\s+.*$", "", body, flags=re.MULTILINE)
    for min_len in (150, 50, 30):
        for para in body.split("\n\n"):
            cleaned = para.strip()
            if not cleaned or cleaned.startswith(("![", "<!--", ">", "|", "---")):
                continue
            if cleaned.startswith("- ") or cleaned.startswith("* "):
                continue
            # Skip HTML wrapper blocks (centered banners, badge rows).
            # Also catches mid-block continuations: '</a>', '<a href...',
            # '<img...', '<span>', etc. — any paragraph that starts with
            # an HTML tag. Same idea: this is markup chrome, not prose.
            if re.match(r"^</?(?:p|div|center|table|tbody|tr|a|img|span|br|hr)\b",
                        cleaned, re.IGNORECASE):
                continue
            # Also skip if HTML tag chars dominate: paragraph is mostly
            # angle-bracket markup (>20% of chars are inside <...> tags).
            html_tag_chars = sum(len(m.group(0)) for m in re.finditer(r"<[^>]+>", cleaned))
            if html_tag_chars > len(cleaned) * 0.2 and len(cleaned) > 50:
                continue
            # Wikilink-heavy paragraph (Connections section style:
            # '[[A]] · [[B]] · [[C]]' — links to other wiki pages, not
            # prose). Different from markdown-link detection above.
            wikilink_chars = sum(len(m.group(0)) for m in re.finditer(r"\[\[[^\]]+\]\]", cleaned))
            if len(cleaned) > 30 and wikilink_chars > len(cleaned) * 0.4:
                continue
            first_lines = cleaned.split("\n", 5)[:5]
            if sum(1 for line in first_lines if re.match(r"^[a-z][a-z0-9_]*:\s", line)) >= 2:
                continue
            if len(cleaned) < min_len:
                continue
            cleaned = re.sub(r"\s+", " ", cleaned).strip()
            delinked = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", cleaned)
            delinked = re.sub(r"^[\s\W]+", "", delinked)
            if UI_NOISE_DETECT.search(delinked[:120]):
                continue
            link_target_chars = sum(
                len(m.group(0)) for m in re.finditer(r"\[[^\]]*\]\(([^)]+)\)", cleaned)
            )
            if link_target_chars > len(cleaned) * 0.4:
                continue
            non_link_text = re.sub(r"!?\[[^\]]*\]\([^)]+\)", "", cleaned).strip()
            if len(non_link_text) < 30:
                continue
            if TOC_DUMP_DETECT.search(cleaned):
                continue
            if URL_ONLY_DETECT.search(cleaned):
                continue
            bare_url_chars = sum(len(m.group(0)) for m in re.finditer(r"https?://\S+", cleaned))
            if bare_url_chars > len(cleaned) * 0.5:
                continue
            if re.match(r"^(URL|Captured|Authors|Clipped|Content fetched):\s",
                        cleaned, re.IGNORECASE):
                continue
            cleaned = re.sub(r"^(Abstract|Description|Summary|TL;DR)[:\s]+",
                             "", cleaned, flags=re.IGNORECASE)
            cleaned = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", cleaned)
            cleaned = re.sub(r"\s+", " ", cleaned).strip()
            if len(cleaned) >= min_len:
                return smart_truncate(cleaned, MAX_SUMMARY)
    return ""


def extract_summary_from_raw(raw_text: str) -> str:
    """Re-extract a summary from a raw page's body — first substantial
    paragraph, capped at MAX_SUMMARY. Mirrors wiki_page.build_fallback_data
    summary logic but with a longer cap."""
    # Strip frontmatter from raw
    body = raw_text
    if body.startswith("---"):
        m = re.search(r"\n---[\s]*\n", body[3:])
        if m:
            body = body[m.end() + 3 :]
    # Skip the H1 header
    body = re.sub(r"^#\s+.*$", "", body, count=1, flags=re.MULTILINE)
    # Skip metadata bullets (- **Field:** value lines)
    body = re.sub(r"^-\s+\*\*[^*]+\*\*[^\n]*\n", "", body, flags=re.MULTILINE)
    # Skip section headers
    body = re.sub(r"^#+\s+.*$", "", body, flags=re.MULTILINE)
    # Use the module-level UI_NOISE_DETECT so this function stays in
    # sync with looks_truncated and _extract_paragraph. (Earlier bug: a
    # local UI_NOISE_RE here was out of date and let the cookie banner
    # through on MITRE ATT&CK pages.)
    # Two passes: first prefer paragraphs >= 50 chars (the meatier
    # ones); second-pass accept >= 30 chars (catches short
    # single-line descriptions like 'Explanations to key concepts
    # in ML' on ML-Papers-Explained).
    for min_len in (150, 50, 30):
        for para in body.split("\n\n"):
            cleaned = para.strip()
            if not cleaned or cleaned.startswith(("![", "<!--", ">", "|", "---")):
                continue
            if cleaned.startswith("- ") or cleaned.startswith("* "):
                continue
            # Skip HTML wrapper blocks (centered banners, badge rows).
            # When a paragraph starts with <p>, <div>, <center>, etc.,
            # the visible text is usually img alt-text + nav links —
            # not real prose. mem0 README is a textbook example.
            if re.match(r"^<(?:p|div|center|table|tbody|tr)\b", cleaned, re.IGNORECASE):
                continue
            # Skip YAML frontmatter blocks (3+ "key: value" lines)
            first_lines = cleaned.split("\n", 5)[:5]
            if sum(1 for line in first_lines if re.match(r"^[a-z][a-z0-9_]*:\s", line)) >= 2:
                continue
            if len(cleaned) < min_len:
                continue
            cleaned = re.sub(r"\s+", " ", cleaned).strip()

        # Strip markdown links from front to test for UI-noise — pattern
        # like '[Edit](url) [Share](url)' would pass the start-of-line
        # check otherwise. Test against the de-linked version.
        delinked = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", cleaned)
        delinked = re.sub(r"^[\s\W]+", "", delinked)
        if UI_NOISE_DETECT.search(delinked[:120]):
            continue

        # Skip link-heavy paragraphs: if >40% of characters are inside
        # markdown link targets, this is probably a navigation block,
        # not prose.
        link_target_chars = sum(
            len(m.group(0)) for m in re.finditer(r"\[[^\]]*\]\(([^)]+)\)", cleaned)
        )
        if link_target_chars > len(cleaned) * 0.4:
            continue

        # Skip image-link blocks (a paragraph that's just one or two
        # markdown image references).
        non_link_text = re.sub(r"!?\[[^\]]*\]\([^)]+\)", "", cleaned).strip()
        if len(non_link_text) < 30:
            continue
        # Skip TOC / navigation dumps (numbered sections, repeated nav)
        if TOC_DUMP_DETECT.search(cleaned):
            continue
        # Skip URL-only / 'Source: <url>' paragraphs
        if URL_ONLY_DETECT.search(cleaned):
            continue
        bare_url_chars = sum(len(m.group(0)) for m in re.finditer(r"https?://\S+", cleaned))
        if bare_url_chars > len(cleaned) * 0.5:
            continue
        # Strip canonical Source/PDF metadata bullets that legacy raws had
        # before '## Content' (URL: / Captured: / Authors:). These survive
        # the metadata-bullet regex because they don't have ** delimiters.
        if re.match(r"^(URL|Captured|Authors|Clipped|Content fetched):\s", cleaned, re.IGNORECASE):
            continue

        # Strip leading "Abstract:" / "Description:" labels
        cleaned = re.sub(r"^(Abstract|Description|Summary|TL;DR)[:\s]+",
                         "", cleaned, flags=re.IGNORECASE)

        # Strip the markdown links themselves so dashboard cells aren't
        # cluttered with bracket-syntax. Keep the link text, drop the URL.
        cleaned = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()

        if len(cleaned) >= 50:
            return smart_truncate(cleaned, MAX_SUMMARY)
    return ""


UI_NOISE_DETECT = re.compile(
    r"^(Edit|Oops|Loading|Sign in|Log in|Share|Subscribe|Follow|"
    r"See more|Show more|Continue reading|Comments|Reply|Repost|"
    r"Skip to content|Jump to|Cookie|This website utilizes|This site uses cookies|"
    # GitHub README CTAs (#144) — '⭐ If you find this useful, please star it'
    r"⭐|If you (?:find|like) this (?:repo|repository|project|tool)|"
    r"please (?:star|consider staring|consider starring))\b|"
    r"(Something went wrong|please (?:try again|enable|update)|"
    r"we couldn't|we cannot|access denied|requires? (?:authentication|authorization|login)|access to this page requires|"
    r"404|page not found|not available|"
    # Cookie banner / privacy notice content
    r"cookies to enable essential site|Privacy Policy Accept Deny|"
    # Table of contents / nav dumps (multiple "Jump to" or numbered sections)
    r"Jump to navigation Jump to search|Expand All Collapse All|"
    # GitHub repo CTAs anywhere in first paragraph
    r"consider stari?ng it|star this repo|sponsor this project|"
    # Geo / language / experience selectors (Deloitte, etc.)
    r"selected the wrong (?:experience|region|country)|please change it above|"
    # Academic UI (PubMed, NCBI) — 'Full text links Cite Display options'
    r"Full text links\s+Cite|Display options\s+Display options|"
    # 'Save citation', 'Add to favorites', etc.
    r"Save citation|Add to favorites|"
    # Prompt-injection attempts (defense in depth — keep these out of
    # summary cells; the script never EXECUTES them, but it shouldn't
    # display them either)
    r"disregard previous instructions|ignore (?:all )?(?:previous|prior) instructions)",
    re.IGNORECASE,
)
# Detects TOC/menu paragraphs: many short numbered sections like
# '1 Overview 2 Introduction 3 Metrics' or repeated nav fragments.
TOC_DUMP_DETECT = re.compile(
    r"(?:\b\d+(?:\.\d+)?\s+[A-Z][a-z]+\s+){5,}|"  # 5+ "1 Overview 2 Intro" patterns
    r"(?:Jump to\s+\w+\s+){2,}|"
    r"(?:Tactics\s+Techniques\s+){2,}|"
    # ATT&CK / matrix style: 'Reconnaissance& Resource Development& Initial Access&' (3+ ampersand-terminated phrases)
    r"(?:[A-Z][A-Za-z\s]+&\s+){3,}|"
    # Many short capitalized phrases separated by spaces (menu items): 'ATLAS Data AI Security 101 ATLAS Glossary'
    r"(?:ATLAS\s+\w+\s+){3,}|"
    # 'Filter by Maturity Feasible Demonstrated Realized' — 3+ single-word labels
    r"Filter by\s+\w+(?:\s+\w+){2,}",
    re.IGNORECASE,
)
# Detects link-only or 'Source: <url>' pattern paragraphs.
URL_ONLY_DETECT = re.compile(
    r"^\**Source:?\**\s*https?://|"
    r"^https?://\S+\s*$",
    re.IGNORECASE,
)


def looks_truncated(s: str) -> bool:
    """Heuristic: should this summary be re-synth'd?"""
    if not s:
        return False
    # Truncation marker (ellipsis or three dots)
    if any(s.rstrip().endswith(m) for m in TRUNCATION_MARKERS):
        return True
    # YAML frontmatter leaked into summary (#142)
    if s.startswith('---') or 'title:' in s[:80] or 'captured_at:' in s[:200]:
        return True
    # UI-noise summary — also test de-linked form
    delinked = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", s)
    delinked = re.sub(r"^[\s\W]+", "", delinked)
    if UI_NOISE_DETECT.search(delinked[:120]):
        return True
    # Link-heavy summary (>40% link-target chars)
    link_chars = sum(len(m.group(0)) for m in re.finditer(r"\[[^\]]*\]\(([^)]+)\)", s))
    if len(s) > 30 and link_chars > len(s) * 0.4:
        return True
    # Bare URL or 'Source: <url>' pattern
    if URL_ONLY_DETECT.search(s):
        return True
    # Bare URL takes >50% of content
    bare_url_chars = sum(len(m.group(0)) for m in re.finditer(r"https?://\S+", s))
    if len(s) > 30 and bare_url_chars > len(s) * 0.5:
        return True
    # TOC / navigation dump
    if TOC_DUMP_DETECT.search(s):
        return True
    # Length matches old hard caps exactly (200, 500): legacy hard-cut.
    # No terminator-check — if length is exactly at a cap, it was cut
    # regardless of what character happened to land at the boundary.
    if len(s) in (200, 500):
        return True
    # Length close to old caps AND doesn't end with terminal punct
    if (195 <= len(s) <= 205 or 495 <= len(s) <= 505) and \
       not s.rstrip().endswith((".", "!", "?", '"', ")", "…")):
        return True
    # Mid-word truncation: last word is suspiciously short (< 5 chars)
    # AND isn't a common short word AND no terminal punct.
    s_strip = s.rstrip()
    if (len(s_strip) > 100
            and not s_strip.endswith(("…", '"', ".", "!", "?", ")"))
            and s_strip[-1].isalpha()):
        last_word = s_strip.rsplit(None, 1)[-1]
        common_short = {
            "i", "is", "a", "an", "in", "on", "at", "to", "of", "or",
            "by", "be", "as", "if", "it", "no", "so", "up", "us", "we",
            "the", "and", "for", "but", "you", "all", "any", "out",
            "can", "had", "has", "her", "him", "his", "its", "may",
            "new", "now", "old", "one", "our", "two", "use", "way",
            "who", "why",
        }
        if (len(last_word) < 5 and last_word.lower() not in common_short
                and last_word.isalpha() and last_word.islower()):
            return True
    # Wikipedia citation marker ([11], [22], etc.) at end
    if re.search(r'\[\d+\]\s*$', s_strip):
        return True
    # HTML-tag-heavy summary (mem0's '<p align=center><img>')
    html_chars = sum(len(m.group(0)) for m in re.finditer(r"<[^>]+>", s))
    if len(s) > 30 and html_chars > len(s) * 0.2:
        return True
    # Wikilink-heavy ('[[A]] · [[B]] · [[C]]')
    wikilink_chars = sum(len(m.group(0)) for m in re.finditer(r"\[\[[^\]]+\]\]", s))
    if len(s) > 30 and wikilink_chars > len(s) * 0.4:
        return True
    # Metadata-key-value dump (LinkedIn-style 'Author: ... Full walkthrough: ...
    # Inspired by: ...' or MITRE-style 'Tactic ID: ... Created: ... Last
    # Modified: ...'). 3+ short 'Key: value' fragments.
    if len(re.findall(r'\b[A-Z][a-zA-Z ]{3,30}:\s', s)) >= 3:
        return True
    # Short summary (< 100 chars) — flag for re-extraction
    if len(s_strip) < 100:
        return True
    return False


def update_summary_field(wiki_path: Path, new_summary: str, dry_run: bool = False) -> bool:
    """Replace the summary: line in the wiki file's frontmatter, OR insert
    one before the closing '---' if the file has no summary field yet
    (e.g. rowboat had a wiki page with raw_path/title/tags but no
    summary line). Returns True if a change was made."""
    text = wiki_path.read_text(encoding="utf-8")
    new_text, count = re.subn(
        r'^summary:\s*"[^"\n]*"\s*$',
        f'summary: "{new_summary}"',
        text,
        count=1,
        flags=re.MULTILINE,
    )
    if count == 0:
        # Try unquoted form
        new_text, count = re.subn(
            r"^summary:\s*[^\n]*$",
            f'summary: "{new_summary}"',
            text,
            count=1,
            flags=re.MULTILINE,
        )
    if count == 0:
        # No existing summary line — insert before the closing '---'
        # of the frontmatter block.
        if text.startswith("---"):
            end_match = re.search(r"\n---[\s]*\n", text[3:])
            if end_match:
                insert_at = end_match.start() + 3
                # Ensure newline separation: if the char immediately
                # before insert_at isn't '\n', prepend one (handles the
                # case where the previous frontmatter value continues
                # without a trailing newline before '---').
                prefix = "\n" if (insert_at > 0 and text[insert_at - 1] != "\n") else ""
                new_text = (
                    text[:insert_at]
                    + prefix
                    + f'summary: "{new_summary}"\n'
                    + text[insert_at:]
                )
                count = 1
    if count == 0 or new_text == text:
        return False
    if not dry_run:
        wiki_path.write_text(new_text, encoding="utf-8")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would change without writing files")
    parser.add_argument("--limit", type=int, default=0,
                        help="Stop after processing N pages (0 = all)")
    parser.add_argument("--force", action="store_true",
                        help="Re-synth even if existing summary doesn't look truncated")
    args = parser.parse_args()

    if not WIKI_FORMAT.is_dir():
        print(f"Vault not found at {WIKI_FORMAT}", file=sys.stderr)
        return 1

    stats = {"scanned": 0, "skipped": {}, "updated": []}

    def skip(reason: str) -> None:
        stats["skipped"][reason] = stats["skipped"].get(reason, 0) + 1

    for wiki_path in WIKI_FORMAT.rglob("*.md"):
        if args.limit and len(stats["updated"]) >= args.limit:
            break
        if wiki_path.name in ("_Contents.md", "_TEMPLATE.md"):
            skip("toc-or-template")
            continue
        stats["scanned"] += 1

        text = wiki_path.read_text(encoding="utf-8")
        fm, _, _ = parse_frontmatter(text)
        if not fm:
            skip("no-frontmatter")
            continue
        if str(fm.get("redirect", "")).lower() == "true":
            skip("redirect-stub")
            continue

        existing = fm.get("summary", "") or ""
        # Empty/missing summary should be treated as "bad" so we attempt
        # to extract from raw — was previously skipped as "no-existing".
        if not existing:
            existing = ""  # proceed; existing_bad will be True via empty check
        elif not args.force and not looks_truncated(existing):
            # Also flag if summary is just a Source line (canonical or
            # emoji-prefix forms). Was leaking through earlier.
            if not re.match(r"^[^a-zA-Z0-9]*Source[\s\S]*Local Copy", existing):
                skip("not-truncated")
                continue

        raw_path = fm.get("raw_path", "")
        if not raw_path:
            skip("no-raw-path")
            continue
        raw_full = VAULT / raw_path
        if not raw_full.is_file():
            skip("raw-missing")
            continue

        try:
            raw_text = raw_full.read_text(encoding="utf-8", errors="replace")
        except OSError:
            skip("raw-read-error")
            continue

        # Prefer wiki body extraction (LLM-curated) over raw extraction
        # (page chrome). Fall back to raw if wiki body produces nothing.
        wiki_text = wiki_path.read_text(encoding="utf-8", errors="replace")
        new_summary = extract_summary_from_wiki_body(wiki_text)
        if not new_summary:
            new_summary = extract_summary_from_raw(raw_text)
        if not new_summary:
            skip("no-summary-extracted")
            continue
        # Special case: if existing summary is YAML-corrupted (#142),
        # always replace regardless of length — even a similar-length
        # real summary is a strict improvement over '--- title: "..." ...'
        existing_is_yaml = (
            existing.startswith('---')
            or 'title:' in existing[:80]
            or 'captured_at:' in existing[:200]
        )

        # Sub-special case: the YAML-corrupted summary contains a NESTED
        # summary: '...' field (the original real summary that was wrapped
        # by the bug). Extract it as the new_summary preference — it's the
        # authoritatively-correct text, no raw fetching needed.
        if existing_is_yaml:
            nested = re.search(
                r"summary:\s*'([^']+)'|summary:\s*\"([^\"]+)\"",
                existing,
            )
            if nested:
                recovered = (nested.group(1) or nested.group(2)).strip()
                if len(recovered) > 30:
                    new_summary = recovered

        # Allow replacement when existing is structurally bad: truncation
        # marker, link-heavy, or matches UI noise. Even shorter new
        # extraction is a structural improvement.
        existing_truncated = any(
            existing.rstrip().endswith(m) for m in TRUNCATION_MARKERS
        )
        existing_link_chars = sum(
            len(m.group(0)) for m in re.finditer(r"\[[^\]]*\]\([^)]+\)", existing)
        )
        existing_link_heavy = (
            len(existing) > 30 and existing_link_chars > len(existing) * 0.4
        )
        existing_delinked = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", existing)
        existing_delinked = re.sub(r"^[\s\W]+", "", existing_delinked)
        existing_ui_noise = bool(UI_NOISE_DETECT.search(existing_delinked[:120]))
        existing_at_legacy_cap = len(existing) in (200, 500)
        existing_toc = bool(TOC_DUMP_DETECT.search(existing))
        existing_url_only = bool(URL_ONLY_DETECT.search(existing))
        existing_bare_url_chars = sum(
            len(m.group(0)) for m in re.finditer(r"https?://\S+", existing)
        )
        existing_bare_url_heavy = (
            len(existing) > 30 and existing_bare_url_chars > len(existing) * 0.5
        )
        # Source-line summary (canonical or emoji-prefix forms)
        existing_source_line = bool(
            re.match(r"^[^a-zA-Z0-9]*Source[\s\S]*Local Copy", existing)
            or re.match(r"^Source\b", existing)
        )
        # HTML-tag-heavy summary (mem0's '<p align=center><img...>')
        existing_html_chars = sum(
            len(m.group(0)) for m in re.finditer(r"<[^>]+>", existing)
        )
        existing_html_heavy = (
            len(existing) > 30 and existing_html_chars > len(existing) * 0.2
        )
        # Empty or trivial (just dashes/whitespace)
        existing_empty = not existing.strip() or existing.strip() in ("-", "—", "...", "…")
        # Short existing AND new extraction has much more content. The
        # "Claude 4.6 — There are three model tiers" case: 53 chars
        # existing vs 600+ chars from the next paragraph.
        existing_too_terse = (
            len(existing) < 100 and len(new_summary) > len(existing) * 3 + 100
        )
        existing_bad = (existing_is_yaml or existing_truncated or existing_link_heavy
                        or existing_ui_noise or existing_at_legacy_cap
                        or existing_toc or existing_url_only or existing_bare_url_heavy
                        or existing_source_line or existing_empty or existing_too_terse
                        or existing_html_heavy)
        if not existing_bad and len(new_summary) <= len(existing) + 30:
            skip("no-meaningful-improvement")
            continue
        # Escape for YAML
        escaped = new_summary.replace('"', "'")
        if update_summary_field(wiki_path, escaped, dry_run=args.dry_run):
            stats["updated"].append((wiki_path.name, len(existing), len(escaped)))

    print(f"Scanned: {stats['scanned']}")
    for reason, count in sorted(stats["skipped"].items(), key=lambda kv: -kv[1]):
        print(f"  Skipped ({reason}): {count}")
    print(f"Updated: {len(stats['updated'])}")
    if args.dry_run:
        print("(dry run — no files written)")
    for name, old_len, new_len in stats["updated"][:10]:
        print(f"  {old_len:>3} → {new_len:>3} chars: {name}")
    if len(stats["updated"]) > 10:
        print(f"  ... and {len(stats['updated']) - 10} more")
    return 0


if __name__ == "__main__":
    sys.exit(main())
