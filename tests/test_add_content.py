"""kb add-content: ingest already-extracted text as a raw + wiki page.

This is the CLI face of the MCP kb_add_content tool. After this lands, the tool
shells out to `kb add-content`, so cmd_add_content is the SINGLE source of truth
for content ingest — no inlined-in-the-server duplicate that can drift (the
duplicate had a latent `clean_title` NameError in its success path).

Hermetic: writes into a throwaway vault, no network, reindex disabled.

Run from vault root:
    python3 -m pytest tests/test_add_content.py -v
"""
from __future__ import annotations

import io
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LIB = ROOT / "bin" / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

import cmd_add_content as ac  # type: ignore  # noqa: E402


def _vault() -> Path:
    tmp = Path(tempfile.mkdtemp(prefix="kb-addcontent-"))
    for d in ("wiki", "wiki/topics", "raw", "inbox"):
        (tmp / d).mkdir(parents=True, exist_ok=True)
    return tmp


class IngestContent(unittest.TestCase):
    def setUp(self):
        self.v = _vault()
        self.addCleanup(shutil.rmtree, self.v, ignore_errors=True)

    def test_creates_raw_and_wiki(self):
        msg = ac.ingest_content(
            self.v, url="https://example.com/a", title="My Title",
            content="# My Title\n\nA substantive body about transformers and agents.",
            reindex=False, today="2026-06-04",
        )
        self.assertIn("Created:", msg)
        self.assertIn("Raw:", msg)
        self.assertIn("Title: My Title", msg)  # uses the real title, not undefined clean_title
        self.assertTrue(list((self.v / "raw").rglob("*.md")), "raw page written")
        self.assertTrue(list((self.v / "wiki").rglob("*.md")), "wiki page written")

    def test_empty_content_raises(self):
        with self.assertRaises(ac.AddContentError):
            ac.ingest_content(self.v, url="u", title="t", content="", reindex=False)

    def test_dedup_by_url(self):
        ac.ingest_content(self.v, url="https://example.com/dup", title="Dup",
                          content="body about security and exploit",
                          reindex=False, today="2026-06-04")
        msg2 = ac.ingest_content(self.v, url="https://example.com/dup", title="Dup",
                                 content="body about security and exploit",
                                 reindex=False, today="2026-06-04")
        self.assertIn("Already in KB", msg2)

    def test_auto_tags_from_keywords(self):
        ac.ingest_content(self.v, url="https://example.com/sec", title="Sec Post",
                          content="a deep dive into vulnerability and exploit and pentest",
                          source_type="webpage", reindex=False, today="2026-06-04")
        text = next((self.v / "wiki").rglob("*.md")).read_text(encoding="utf-8")
        self.assertIn("security", text)  # tag derived from keywords


class CliHandler(unittest.TestCase):
    def setUp(self):
        self.v = _vault()
        self.addCleanup(shutil.rmtree, self.v, ignore_errors=True)

    def test_handle_reads_stdin(self):
        argv = ["--url", "https://example.com/s", "--title", "Stdin Post",
                "--content", "-", "--no-index"]
        old_stdin = sys.stdin
        sys.stdin = io.StringIO("a body about obsidian vault wikilink")
        try:
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = ac.handle(argv, str(self.v))
        finally:
            sys.stdin = old_stdin
        self.assertEqual(rc, 0)
        self.assertIn("Created:", buf.getvalue())

    def test_handle_content_file(self):
        cf = self.v / "paste.txt"
        cf.write_text("body about prompt and few-shot", encoding="utf-8")
        argv = ["--url", "https://example.com/f", "--title", "File Post",
                "--content-file", str(cf), "--no-index"]
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = ac.handle(argv, str(self.v))
        self.assertEqual(rc, 0)
        self.assertIn("Created:", buf.getvalue())

    def test_handle_missing_content_errors(self):
        argv = ["--url", "u", "--title", "t", "--content", "", "--no-index"]
        rc = ac.handle(argv, str(self.v))
        self.assertEqual(rc, 1)


class ShellEndToEnd(unittest.TestCase):
    """Proves `kb add-content` is reachable as a plain shell command with piped
    stdin — the whole point of the feature (external callers shell out)."""

    def setUp(self):
        # COPY (not symlink) bin/ so the dispatcher's Path.resolve() roots at the
        # temp vault, per the test_kb_parity.py gotcha. Only copy what dispatch
        # needs (lib/ + kb), skipping the 385KB frozen legacy binaries.
        self.v = _vault()
        self.addCleanup(shutil.rmtree, self.v, ignore_errors=True)
        (self.v / "bin").mkdir(exist_ok=True)
        shutil.copytree(ROOT / "bin" / "lib", self.v / "bin" / "lib")
        shutil.copy(ROOT / "bin" / "kb", self.v / "bin" / "kb")
        (self.v / "CLAUDE.md").write_text("# vault marker\n", encoding="utf-8")

    def test_shell_invocation_with_stdin(self):
        proc = subprocess.run(
            [sys.executable, str(self.v / "bin" / "kb"), "add-content",
             "--url", "https://example.com/e2e", "--title", "E2E Post",
             "--content", "-", "--no-index"],
            input="a body about knowledge graph and rag retrieval",
            capture_output=True, text=True, cwd=str(self.v), timeout=60,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("Created:", proc.stdout)
        self.assertTrue(list((self.v / "wiki").rglob("*.md")), "wiki page written via shell")


if __name__ == "__main__":
    unittest.main()
