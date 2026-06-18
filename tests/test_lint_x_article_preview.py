import os, sys, shutil, tempfile, subprocess, unittest
from pathlib import Path
KB = os.path.join(os.path.dirname(__file__), "..")

# Bodies are padded past the 500-byte thin-orphan threshold so lint's
# thin-raw auto-fix doesn't trash them before the preview-only check runs.
_PAD = ("\n\nPadding paragraph to clear the thin-orphan threshold so this raw "
        "survives the lint pass and reaches the preview-only check. " * 6)
PREVIEW = ('---\ntitle: "A"\nsource: "https://x.com/u/status/1"\n'
           'clipped_via: "tweet-syndication"\n---\n'
           '# A\n\nThis is a long-form X Article. Read the full piece: '
           '[https://x.com/i/article/9](https://x.com/i/article/9)\n' + _PAD)
FULL = ('---\ntitle: "B"\nsource: "https://x.com/u/status/2"\n'
        'clipped_via: "capture-deep"\n---\n# B\n\nFull article body here.\n' + _PAD)


def _run_lint(vault):
    py = os.environ.get("ATHENA_PYTHON", sys.executable)
    # bin/ is COPIED into the vault so bin/kb resolves THIS temp vault as the
    # root (source-mode vault_root() pins the root via __file__).
    return subprocess.run([py, os.path.join(vault, "bin", "kb"), "lint"],
                          cwd=vault, capture_output=True, text=True,
                          env=dict(os.environ, KB_ROOT=vault)).stdout


class XArticlePreviewLint(unittest.TestCase):
    def _vault(self, body):
        d = tempfile.mkdtemp()
        Path(d, "CLAUDE.md").write_text("# vault")
        Path(d, "RULES.md").write_text("# RULES\n")
        for sub in ("wiki/format/webpages", "inbox"):
            os.makedirs(os.path.join(d, sub), exist_ok=True)
        art = Path(d, "raw", "webpages", "artifacts"); art.mkdir(parents=True)
        Path(art, "x.md").write_text(body)
        shutil.copytree(os.path.join(KB, "bin"), os.path.join(d, "bin"))
        return d

    def test_preview_only_surfaced(self):
        out = _run_lint(self._vault(PREVIEW))
        self.assertIn("preview-only", out.lower())

    def test_full_capture_not_surfaced(self):
        out = _run_lint(self._vault(FULL))
        self.assertNotIn("preview-only", out.lower())


if __name__ == "__main__":
    unittest.main()
