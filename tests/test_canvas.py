"""Tests for bin/lib/cmd_canvas.py — `kb canvas <topic>` radial Canvas generator.

Run from vault root:  python3 -m pytest tests/test_canvas.py -v
"""
from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

LIB = Path(__file__).resolve().parent.parent / "bin" / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

import cmd_canvas  # noqa: E402


_TOPIC = """\
---
title: Test Topic
source_type: topic
tags:
  - topic
related:
  - "[[Alpha Paper]]"
  - "[[Beta Paper]]"
  - "[[Gamma Repo]]"
---
# Test Topic

## Connections

- [[Alpha Paper]] — a paper
"""

_PAGE = """\
---
title: {title}
source_type: {stype}
---
Body of {title}.
"""


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _build_vault(base: Path) -> str:
    """Create a tiny vault: a topic page linking to 2 papers + 1 repo, plus one
    unresolvable link to exercise the skip path."""
    root = base
    (root / "CLAUDE.md").write_text("# vault marker\n", encoding="utf-8")
    _write(root / "wiki" / "topics" / "Test Topic.md", _TOPIC)
    _write(root / "wiki" / "format" / "papers" / "Alpha Paper.md",
           _PAGE.format(title="Alpha Paper", stype="paper"))
    _write(root / "wiki" / "format" / "papers" / "Beta Paper.md",
           _PAGE.format(title="Beta Paper", stype="paper"))
    _write(root / "wiki" / "format" / "repos" / "Gamma Repo.md",
           _PAGE.format(title="Gamma Repo", stype="repo"))
    return str(root)


def _run(argv, root):
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = cmd_canvas.handle(argv, root)
    return rc, buf.getvalue()


class CanvasGenerator(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = _build_vault(Path(self._tmp.name))
        self.out = Path(self.root) / "wiki" / "canvas" / "Test Topic.canvas"

    def tearDown(self):
        self._tmp.cleanup()

    def _generate(self):
        rc, _ = _run(["Test Topic"], self.root)
        self.assertEqual(rc, 0)
        self.assertTrue(self.out.exists(), "canvas file was not written")
        return json.loads(self.out.read_text(encoding="utf-8"))

    def test_a_file_exists_and_valid_json(self):
        data = self._generate()
        self.assertIn("nodes", data)
        self.assertIn("edges", data)

    def test_b_center_node_references_topic(self):
        data = self._generate()
        centers = [n for n in data["nodes"] if n["id"] == "center"]
        self.assertEqual(len(centers), 1)
        self.assertEqual(centers[0]["file"], "wiki/topics/Test Topic.md")
        self.assertEqual(centers[0]["color"], "5")
        self.assertEqual((centers[0]["x"], centers[0]["y"]), (0, 0))

    def test_c_one_node_per_resolved_related(self):
        data = self._generate()
        neighbors = [n for n in data["nodes"] if n["id"] != "center"]
        # 3 related links all resolve; 0 unresolved here.
        self.assertEqual(len(neighbors), 3)
        files = {n["file"] for n in neighbors}
        self.assertEqual(files, {
            "wiki/format/papers/Alpha Paper.md",
            "wiki/format/papers/Beta Paper.md",
            "wiki/format/repos/Gamma Repo.md",
        })

    def test_d_one_edge_per_neighbor_from_center(self):
        data = self._generate()
        neighbors = [n for n in data["nodes"] if n["id"] != "center"]
        self.assertEqual(len(data["edges"]), len(neighbors))
        for e in data["edges"]:
            self.assertEqual(e["fromNode"], "center")
        targets = {e["toNode"] for e in data["edges"]}
        self.assertEqual(targets, {n["id"] for n in neighbors})

    def test_e_colors_match_type(self):
        data = self._generate()
        by_file = {n["file"]: n.get("color") for n in data["nodes"]}
        self.assertEqual(by_file["wiki/format/papers/Alpha Paper.md"], "4")
        self.assertEqual(by_file["wiki/format/papers/Beta Paper.md"], "4")
        self.assertEqual(by_file["wiki/format/repos/Gamma Repo.md"], "2")

    def test_f_missing_topic_returns_1_no_file(self):
        rc, out = _run(["Nonexistent Topic"], self.root)
        self.assertEqual(rc, 1)
        self.assertIn("No topic page found", out)
        self.assertIn("kb list --topics", out)
        self.assertFalse(
            (Path(self.root) / "wiki" / "canvas" / "Nonexistent Topic.canvas").exists()
        )

    def test_g_idempotent_byte_identical(self):
        self._generate()
        first = self.out.read_bytes()
        rc, _ = _run(["Test Topic"], self.root)
        self.assertEqual(rc, 0)
        second = self.out.read_bytes()
        self.assertEqual(first, second, "rerun produced a different file")

    def test_unresolved_link_skipped(self):
        # Add a related link to a page that does not exist; it must be skipped
        # (logged) rather than crash or produce a node.
        topic = Path(self.root) / "wiki" / "topics" / "Test Topic.md"
        text = topic.read_text(encoding="utf-8").replace(
            '  - "[[Gamma Repo]]"',
            '  - "[[Gamma Repo]]"\n  - "[[Does Not Exist]]"',
        )
        topic.write_text(text, encoding="utf-8")
        rc, out = _run(["Test Topic"], self.root)
        self.assertEqual(rc, 0)
        self.assertIn("skipped unresolved link", out)
        data = json.loads(self.out.read_text(encoding="utf-8"))
        files = {n["file"] for n in data["nodes"] if n["id"] != "center"}
        self.assertNotIn("Does Not Exist", "".join(files))


if __name__ == "__main__":
    unittest.main()
