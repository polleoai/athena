#!/usr/bin/env python3
"""Rewrite relative image URLs in a fetched GitHub README to absolute URLs.

GitHub's API returns the README's raw markdown verbatim. Images in the README
use repo-relative paths ("wiki/meta/cover.gif", "./images/foo.png") that only
resolve on github.com. When Athena stores the README locally, those paths 404.

This script rewrites both `<img src="...">` HTML tags and `![](...)` markdown
images to absolute URLs at raw.githubusercontent.com/<owner>/<repo>/<branch>/...
so Obsidian can fetch them on render.

Usage:
    python3 github_readme_postprocess.py <owner> <repo> <branch> <file>
"""

import re
import sys
from pathlib import Path
from urllib.parse import urljoin


def rewrite_readme(owner: str, repo: str, branch: str, readme_path: Path) -> int:
    """Rewrite the README in place. Returns the number of rewrites applied."""
    base = f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/"
    content = readme_path.read_text(encoding="utf-8", errors="ignore")
    count = 0

    def resolve(src: str) -> str:
        nonlocal count
        if re.match(r"^(https?:|data:|mailto:|#|//)", src, re.IGNORECASE):
            return src
        # Strip leading ./ and any leading /
        src = src.lstrip("/")
        if src.startswith("./"):
            src = src[2:]
        # Some repos use "blob/main/path" GitHub URLs in src — skip rewriting those
        count += 1
        return urljoin(base, src)

    # HTML <img src="...">
    content = re.sub(
        r'(<img[^>]*\bsrc=")([^"]+)(")',
        lambda m: m.group(1) + resolve(m.group(2)) + m.group(3),
        content,
        flags=re.IGNORECASE,
    )

    # Markdown ![alt](src) — only the bare URL form; leave titled/complex forms alone
    content = re.sub(
        r'(!\[[^\]]*\]\()([^)\s]+)(\))',
        lambda m: m.group(1) + resolve(m.group(2)) + m.group(3),
        content,
    )

    content = _normalize_table_rows(content)
    readme_path.write_text(content, encoding="utf-8")
    return count


def _normalize_table_rows(content: str) -> str:
    """GitHub's markdown parser is flexible about table rows — a line can omit
    the leading '|' if a previous row in the same table established the column
    structure. Obsidian's parser is strict: every row needs a leading '|', or
    the parser stops rendering the table and the rest of the rows show as
    literal text with visible pipes.

    NVIDIA/RULER's README hits this — the Llama2 row ends with '|85.6|' and
    every subsequent row starts with '[Jamba-1.5-large](url)|256k|...' (no
    leading '|'). GitHub renders the full table; Obsidian renders only the
    first row and shows the rest as broken pipe-separated text.

    Fix: when we detect a table (header line + separator line), normalize
    every subsequent line that looks like a table row but lacks the leading
    '|' until we hit a clearly-non-table line (blank, heading, no pipes).

    Heuristic for "looks like a table row":
      - line has 4+ '|' characters (real table rows usually have many cells)
      - line is not a blank line, heading, list item, code fence
    """
    lines = content.split("\n")
    out = []
    in_table = False
    sep_re = re.compile(r"^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)+\|?\s*$")
    row_start_re = re.compile(r"^\s*\|")
    likely_row_re = re.compile(r"^[^\s#>`].*\|.*\|.*\|.*\|")  # 4+ pipes, not heading/list/code

    for i, line in enumerate(lines):
        # Detect entry into a table: a row line followed by a separator line.
        if not in_table:
            stripped = line.strip()
            next_line = lines[i + 1] if i + 1 < len(lines) else ""
            if (row_start_re.match(line) or likely_row_re.match(stripped)) and sep_re.match(next_line):
                in_table = True
            out.append(line)
            continue

        # Inside a table — exit on blank line, heading, code fence, or any
        # line that clearly isn't a table row.
        stripped = line.strip()
        if not stripped or stripped.startswith(("#", "```", "<!--", ">")):
            in_table = False
            out.append(line)
            continue

        # Table row: ensure BOTH leading and trailing '|'. NVIDIA/RULER's
        # README has the Jamba-1.5-mini row ending in '94.8 **(3rd)**' (no
        # trailing pipe) — Obsidian's strict parser tolerates the row but
        # gets confused on subsequent rows and stops rendering the rest of
        # the table. Same fix as the leading-pipe normalization, applied
        # to the right edge.
        if stripped.count("|") < 3:
            in_table = False
            out.append(line)
            continue
        indent = line[: len(line) - len(line.lstrip())]
        body = line.lstrip().rstrip()
        # Trailing whitespace after the last cell is fine; missing
        # trailing pipe is not. `rstrip()` on body removes any trailing
        # whitespace so we can check the last char cleanly.
        if not body.startswith("|"):
            body = "|" + body
        if not body.endswith("|"):
            body = body + "|"
        out.append(indent + body)

    return "\n".join(out)


def main() -> int:
    if len(sys.argv) != 5:
        print(f"usage: {sys.argv[0]} <owner> <repo> <branch> <file>", file=sys.stderr)
        return 2
    owner, repo, branch, file = sys.argv[1:]
    path = Path(file)
    if not path.exists():
        print(f"error: file not found: {file}", file=sys.stderr)
        return 1
    n = rewrite_readme(owner, repo, branch, path)
    print(f"rewrote {n} image URLs in {file}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
