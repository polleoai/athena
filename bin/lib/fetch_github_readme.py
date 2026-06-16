#!/usr/bin/env python3
"""Fetch a GitHub repo's README (and light metadata) without the `gh` CLI.

The Obsidian plugin captures GitHub repos by DOM-walking the rendered page,
which mangles README images (forces width=600 on every <img>, drops ![]()
thumbnails, orphans [<img>](link) brackets). The CLI `kb-capture` does it
right — it fetches the actual README via `gh api` and rewrites relative
image paths to absolute raw.githubusercontent URLs — but end-user machines
rarely have `gh` installed/authed.

This helper gives the plugin the same clean result with no `gh` dependency:
it fetches the README over plain HTTP from the public GitHub REST API
(falling back to raw.githubusercontent.com), reuses
`github_readme_postprocess.rewrite_readme_text` for image-URL resolution
(single source of truth), and emits a complete raw .md with frontmatter.

Usage:
    python3 fetch_github_readme.py <owner> <repo> <source_url>

On success: prints the raw markdown to stdout, exits 0.
On failure (private repo, offline, rate-limited): prints nothing, exits 1 —
the caller falls back to its browser-capture path.
"""

import base64
import json
import re
import sys
import urllib.error
import urllib.request

from github_readme_postprocess import rewrite_readme_text

_UA = "athena-kb (+https://github.com/polleoai/athena)"
_TIMEOUT = 15
_MAX_BYTES = 8 * 1024 * 1024  # cap the response (untrusted README/JSON, unbounded)


def _http_get(url: str, *, accept: str = "application/vnd.github+json") -> bytes:
    """GET a URL with a GitHub-friendly User-Agent. Raises on HTTP/network error."""
    req = urllib.request.Request(url, headers={"User-Agent": _UA, "Accept": accept})
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:  # noqa: S310 (https only below)
        raw = resp.read(_MAX_BYTES + 1)
        if len(raw) > _MAX_BYTES:
            raise ValueError(f"response exceeds {_MAX_BYTES} bytes")
        return raw


def _api_get_json(url: str) -> dict:
    return json.loads(_http_get(url).decode("utf-8", "replace"))


def fetch_repo_metadata(owner: str, repo: str) -> dict | None:
    """Return repo metadata (default_branch, stars, description, language,
    topics) via the public REST API, or None on any failure."""
    try:
        return _api_get_json(f"https://api.github.com/repos/{owner}/{repo}")
    except (urllib.error.URLError, OSError, ValueError):
        return None


def fetch_readme_markdown(owner: str, repo: str, branch: str) -> str | None:
    """Return the raw README markdown. Tries the API's /readme endpoint
    (base64) first, then raw.githubusercontent.com for the default branch.
    Returns None if neither resolves."""
    # 1) REST /readme endpoint — works for any default README filename.
    try:
        meta = _api_get_json(f"https://api.github.com/repos/{owner}/{repo}/readme")
        content = meta.get("content")
        if content and meta.get("encoding") == "base64":
            return base64.b64decode(content).decode("utf-8", "replace")
    except (urllib.error.URLError, OSError, ValueError):
        pass
    # 2) Fallback: well-known raw paths on the default branch.
    for name in ("README.md", "readme.md", "README.rst", "README"):
        try:
            raw = _http_get(
                f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{name}",
                accept="text/plain",
            )
            text = raw.decode("utf-8", "replace")
            if text.strip():
                return text
        except (urllib.error.URLError, OSError):
            continue
    return None


def _yaml_escape(s: str) -> str:
    # Collapse newlines before quoting so untrusted JSON fields (full_name,
    # language) can't break out of the quoted scalar and inject frontmatter.
    s = re.sub(r"[\r\n]+", " ", str(s or "")).strip()
    return s.replace("\\", "\\\\").replace('"', '\\"')


def build_repo_raw(owner: str, repo: str, url: str) -> str | None:
    """Fetch + assemble a complete raw .md for a GitHub repo, or None on
    failure. Image URLs are rewritten to absolute so Obsidian renders them
    exactly like GitHub does."""
    meta = fetch_repo_metadata(owner, repo)
    if meta is None:
        return None
    branch = meta.get("default_branch") or "main"
    readme = fetch_readme_markdown(owner, repo, branch)
    if not readme:
        return None

    body, _ = rewrite_readme_text(owner, repo, branch, readme)

    full_name = meta.get("full_name") or f"{owner}/{repo}"
    desc = (meta.get("description") or "").strip()
    stars = meta.get("stargazers_count")
    language = (meta.get("language") or "").strip()
    topics = meta.get("topics") or []

    fm = [
        "---",
        f'title: "{_yaml_escape(full_name)}"',
        f'source: "{_yaml_escape(url)}"',
        'clipped_via: "github-readme"',
    ]
    if stars is not None:
        fm.append(f'stars: "{stars}"')
    if language:
        fm.append(f'language: "{_yaml_escape(language)}"')
    fm.append("---")

    meta_lines = [f"# {full_name}", ""]
    if desc:
        meta_lines.append(f"> {desc}")
        meta_lines.append("")
    if topics:
        meta_lines.append("**Topics:** " + ", ".join(topics))
        meta_lines.append("")
    meta_lines.append("## README")
    meta_lines.append("")

    return "\n".join(fm) + "\n\n" + "\n".join(meta_lines) + body + "\n"


def main(argv: list[str]) -> int:
    if len(argv) != 4:
        print(f"usage: {argv[0]} <owner> <repo> <source_url>", file=sys.stderr)
        return 2
    owner, repo, url = argv[1], argv[2].removesuffix(".git"), argv[3]
    raw = build_repo_raw(owner, repo, url)
    if not raw:
        return 1
    sys.stdout.write(raw)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
