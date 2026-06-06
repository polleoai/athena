"""Parity: bin/kb-capture (Python) must match bin/kb-capture-legacy (Bash) on
every DETERMINISTIC, pre-network surface.

bin/kb-capture is the Python port of the last Bash piece on the URL-ingest path
(porting it is what unblocks native-Windows ingest). bin/kb-capture-legacy is
the preserved Bash oracle — never modified, only compared against.

What is byte-parity-proven here (no network touched):
  1. CLI surfaces that exit BEFORE any fetch — `--help`/`-h`, unknown option,
     missing-value-for-flag, scheme validation `die` messages, and
     empty-after-sanitization. Both real scripts are run and their
     stdout+stderr+exit compared.
  2. URL sanitization (markdown-leak strip) — Python `sanitize_url` vs the
     Bash sanitizer heredoc, over a table of leaked forms.
  3. URL canonicalization — both call the SAME bin/lib/url_canonical.py, so we
     assert the Python wrapper's output equals the module's own output.
  4. Type detection — Python `detect_type` vs the EXTRACTED Bash `detect_type`
     function, over a representative URL table.
  5. Slug generation — Python `url_to_slug`/`slug` vs the EXTRACTED Bash
     `url_to_slug`/`slug`/`github_parts`, same table (youtube `video-<id>`,
     arxiv `arxiv-<id>`, repo `owner--repo`, drive, generic webpage, file).

The Bash functions are EXTRACTED from the legacy script (brace-balanced) and
run in an isolated `bash -c` harness — so we compare the genuine oracle logic,
not a re-transcription of it.

NETWORK branches (webpage/repo/paper/video fetch, tweet/LinkedIn/Reddit
resolution, health check, asset download) are NOT exercised here — they are
non-deterministic. They are validated functionally by the controller via the
host net suite (and the existing stubbed-network parity in test_kb_parity.py,
which drives `kb add`/`batch` through a recorded kb-capture).

Self-skips on Windows (the legacy Bash can't run there; Windows correctness is
proven by the argus VM matrix instead).

Run from vault root:
    python3 -m pytest tests/test_kb_capture_parity.py -v
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LEGACY = ROOT / "bin" / "kb-capture-legacy"
PY_CAPTURE = ROOT / "bin" / "kb-capture"


# ── Load bin/kb-capture as an importable module (without running main) ───────
def _load_py_capture() -> types.ModuleType:
    src = PY_CAPTURE.read_text()
    mod = types.ModuleType("kb_capture_under_test")
    mod.__file__ = str(PY_CAPTURE)
    if str(ROOT / "bin" / "lib") not in sys.path:
        sys.path.insert(0, str(ROOT / "bin" / "lib"))
    exec(compile(src, str(PY_CAPTURE), "exec"), mod.__dict__)
    return mod


# ── Extract a brace-balanced Bash function body from the legacy script ───────
def _grab_bash_func(name: str, src: str) -> str:
    one_liner = re.search(rf'^{re.escape(name)}\(\)\s*\{{.*\}}\s*$', src, re.M)
    m = re.search(rf'^{re.escape(name)}\(\)\s*\{{', src, re.M)
    if m is None and one_liner:
        return one_liner.group(0)
    # If the multi-line opener and a same-line close both exist, prefer the
    # opener-based brace walk (handles the multi-line bodies).
    if m is None:
        raise AssertionError(f"bash function {name!r} not found in legacy")
    depth = 0
    start = m.start()
    for j in range(m.end() - 1, len(src)):
        if src[j] == "{":
            depth += 1
        elif src[j] == "}":
            depth -= 1
            if depth == 0:
                return src[start:j + 1]
    raise AssertionError(f"unbalanced braces for {name!r}")


def _bash_detect_and_slug(url: str) -> tuple[str, str]:
    """Run the EXTRACTED legacy detect_type + url_to_slug over one URL."""
    src = LEGACY.read_text()
    funcs = "\n".join(
        _grab_bash_func(n, src)
        for n in ("slug", "detect_type", "github_parts", "url_to_slug")
    )
    harness = funcs + (
        '\nt=$(detect_type "$1")\n'
        'echo "$t"\n'
        'echo "$(url_to_slug "$1" "$t")"\n'
    )
    proc = subprocess.run(["/bin/bash", "-c", harness, "_", url],
                          capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    lines = proc.stdout.splitlines()
    return lines[0], (lines[1] if len(lines) > 1 else "")


def _bash_sanitize(url: str) -> str:
    """Run the legacy's embedded python sanitizer heredoc over one URL."""
    code = (
        'import re, sys\n'
        'u = sys.stdin.read().strip()\n'
        'm = re.match(r"^\\[(https?://[^\\]]+)\\]\\((https?://[^)]+)\\)", u)\n'
        'if m:\n    u = m.group(2)\n'
        'u = re.sub(r"^[\\[\\(\\{`\\x27\\"<]+|[\\]\\)\\}`\\x27\\">]+$", "", u)\n'
        'u = re.sub(r"[\\*_.,;!?…]+$", "", u)\n'
        'print(u, end="")\n'
    )
    proc = subprocess.run([sys.executable, "-c", code], input=url,
                          capture_output=True, text=True)
    return proc.stdout


# ── Representative URL tables ────────────────────────────────────────────────
TYPE_SLUG_URLS = [
    # repo
    "https://github.com/openai/whisper",
    "https://github.com/anthropics/anthropic-cookbook/tree/main",
    "https://gist.github.com/someone/abc123",
    # video
    "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    "https://youtu.be/dQw4w9WgXcQ",
    "https://www.youtube.com/shorts/abcDEF123",
    "https://youtube.com/playlist?list=PLrAXtmRdnEQy6nuLMHjMZOz59Oq8B9Ld2",
    "https://www.youtube.com/watch?v=KeepCaseID_99&list=PLxyz",
    # paper
    "https://arxiv.org/abs/2312.00752",
    "https://arxiv.org/abs/2405.21060/",
    "https://drive.google.com/file/d/1AbCdEf2GhIjKlMnOpQrStUvWxYz/view",
    "https://nvlpubs.nist.gov/nistpubs/ai/NIST.AI.600-1.pdf",
    # file types
    "https://example.com/files/report.docx",
    "https://example.com/data/sheet.xlsx",
    "https://example.com/deck.pptx",
    "https://example.com/book.epub",
    # webpage (incl. tweet sub-path, case-sensitivity)
    "https://example.com/some/Long-Path-Here?token=xyz",
    "https://example.com/report.PDF",
    "https://x.com/jack/status/20",
    "https://twitter.com/jack/status/20",
    "https://blog.example.org/2026/05/a-really-long-article-title-that-exceeds-the-window-"
    "and-keeps-going-well-past-one-hundred-and-twenty-characters-to-test-truncation",
]

SANITIZE_URLS = [
    "https://lnkd.in/X**",
    "[https://example.com/a](https://example.com/b)",
    "(https://example.com/c)",
    "<https://example.com/d>",
    "`https://example.com/e`",
    "https://example.com/f.",
    "https://example.com/g…",
    '"https://example.com/h"',
    "https://example.com/normal",
]

CANONICAL_URLS = [
    "https://x.com/jack/status/20?s=21&t=abc",
    "https://www.youtube.com/watch?v=abc&list=PLx&index=3&t=10s",
    "https://arxiv.org/abs/2312.00752v3",
    "https://github.com/openai/whisper/blob/main/README.md",
    "https://example.com/page?utm_source=newsletter&id=5",
    "https://www.linkedin.com/posts/someone_a-post-ugcPost-7300000000000000000-AbCd",
]


@unittest.skipIf(os.name == "nt", "legacy Bash can't run on Windows")
class CaptureDeterministicParity(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.mod = _load_py_capture()

    # ── 1. CLI surfaces that exit before any network ─────────────────────────
    def _run_both(self, args: list[str], stdin: str = "") -> None:
        legacy = subprocess.run(["/bin/bash", str(LEGACY), *args],
                                input=stdin, capture_output=True, text=True, cwd=ROOT)
        py = subprocess.run([sys.executable, str(PY_CAPTURE), *args],
                            input=stdin, capture_output=True, text=True, cwd=ROOT)
        self.assertEqual(legacy.returncode, py.returncode,
                         f"exit differs for {args!r}\nlegacy={legacy.stderr}\npy={py.stderr}")
        self.assertEqual(legacy.stdout, py.stdout, f"stdout differs for {args!r}")
        self.assertEqual(legacy.stderr, py.stderr, f"stderr differs for {args!r}")

    def test_help_long(self):
        self._run_both(["--help"])

    def test_help_short(self):
        self._run_both(["-h"])

    def test_unknown_option(self):
        self._run_both(["--definitely-not-a-flag"])

    def test_missing_value_desc(self):
        self._run_both(["https://example.com/x", "--desc"])

    def test_missing_value_keywords(self):
        self._run_both(["https://example.com/x", "--keywords"])

    def test_unexpected_second_url(self):
        self._run_both(["https://example.com/a", "https://example.com/b"])

    def test_scheme_ftp(self):
        self._run_both(["ftp://example.com/x"])

    def test_scheme_file(self):
        self._run_both(["file:///etc/passwd"])

    def test_scheme_missing(self):
        self._run_both(["example.com/no-scheme"])

    def test_empty_after_sanitization(self):
        # "**" sanitizes to "" -> "URL is empty after sanitization" die.
        self._run_both(["**"])

    # ── 2. URL sanitization parity ───────────────────────────────────────────
    def test_sanitize_parity(self):
        for u in SANITIZE_URLS:
            with self.subTest(url=u):
                self.assertEqual(self.mod.sanitize_url(u), _bash_sanitize(u),
                                 f"sanitize differs for {u!r}")

    # ── 3. Canonicalization parity (both call the same module) ───────────────
    def test_canonicalize_parity(self):
        from url_canonical import canonicalize
        for u in CANONICAL_URLS:
            with self.subTest(url=u):
                self.assertEqual(self.mod.canonicalize_url(u), canonicalize(u).url)

    # ── 4 + 5. Type detection + slug parity (vs extracted Bash funcs) ────────
    def test_type_and_slug_parity(self):
        for u in TYPE_SLUG_URLS:
            with self.subTest(url=u):
                b_type, b_slug = _bash_detect_and_slug(u)
                p_type = self.mod.detect_type(u)
                p_slug = self.mod.url_to_slug(u, p_type)
                self.assertEqual(p_type, b_type, f"type differs for {u!r}")
                self.assertEqual(p_slug, b_slug, f"slug differs for {u!r}")

    # ── slug() primitive parity over tricky inputs ───────────────────────────
    def test_slug_primitive_parity(self):
        src = LEGACY.read_text()
        slug_fn = _grab_bash_func("slug", src)
        harness = slug_fn + '\nslug "$1"\n'
        for s in ["Hello World!", "  Leading-and-Trailing--Dashes  ",
                  "MixedCASE_and.dots", "émigré-ünïcode", "a---b___c",
                  "----", "UPPER", "trailing-"]:
            with self.subTest(text=s):
                proc = subprocess.run(["/bin/bash", "-c", harness, "_", s],
                                      capture_output=True, text=True)
                self.assertEqual(self.mod.slug(s), proc.stdout.rstrip("\n"),
                                 f"slug differs for {s!r}")


if __name__ == "__main__":
    unittest.main()
