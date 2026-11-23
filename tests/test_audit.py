"""#180: a persistent audit trail of destructive kb operations.

Every soft-delete / snapshot (remove, delete, merge, ungroup, rename, move,
attach, detach, lint auto-fixes), every undo and every purge appends one JSON
line to <vault>/.athena/audit.jsonl. A prompt the user declines is logged as
`deny`. The record uses Gryphon's DecisionAuditEntry field names (ts in epoch
ms, tool, args_summary, decision, reason) plus `source`, so an analyzer that
reads Gryphon's audit reads Athena's. The sink never blocks or fails a kb
command. Rotation: past 10 MB the file rolls to audit.1.jsonl … audit.5.jsonl.
"""
from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stderr
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "bin" / "lib"))
import audit  # noqa: E402
import snapshot  # noqa: E402


def _lines(vault):
    p = Path(vault) / ".athena" / "audit.jsonl"
    if not p.is_file():
        return []
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


class Sink(unittest.TestCase):
    def setUp(self):
        self.vault = tempfile.mkdtemp()

    def test_record_shape_matches_gryphon(self):
        before = int(time.time() * 1000)
        self.assertTrue(audit.record(self.vault, "kb remove", "Some Page", "allow", "user-confirmed"))
        (e,) = _lines(self.vault)
        self.assertEqual({k: e[k] for k in ("tool", "args_summary", "decision", "reason", "source")},
                         {"tool": "kb remove", "args_summary": "Some Page", "decision": "allow",
                          "reason": "user-confirmed", "source": "cli"})
        self.assertIsInstance(e["ts"], int)
        self.assertGreaterEqual(e["ts"], before)

    def test_source_comes_from_the_plugin_env(self):
        # the chat surface runs bin/kb with ATHENA_AUDIT_SOURCE=plugin
        os.environ["ATHENA_AUDIT_SOURCE"] = "plugin"
        self.addCleanup(os.environ.pop, "ATHENA_AUDIT_SOURCE", None)
        audit.record(self.vault, "kb undo", "x", "allow", "--yes")
        self.assertEqual(_lines(self.vault)[-1]["source"], "plugin")

    def test_a_chat_confirmation_is_the_reason(self):
        # the plugin asks first, then runs bin/kb with --yes (or a verb with
        # no prompt): the log says the user confirmed in chat, not "--yes"
        os.environ["ATHENA_AUDIT_CONFIRMED"] = "user-confirmed in chat"
        self.addCleanup(os.environ.pop, "ATHENA_AUDIT_CONFIRMED", None)
        audit.record(self.vault, "kb remove", "P", "allow", "--yes")
        page = Path(self.vault) / "wiki" / "x.md"
        page.parent.mkdir(parents=True)
        page.write_text("x")
        snapshot.snapshot_files(self.vault, [], [str(page)], operation="move", description="kb move")
        audit.record(self.vault, "kb remove", "P", "deny", "user-cancelled")
        self.assertEqual([e["reason"] for e in _lines(self.vault)],
                         ["user-confirmed in chat", "user-confirmed in chat", "user-cancelled"])

    def test_appends_one_line_each(self):
        for n in range(3):
            audit.record(self.vault, "kb purge", f"{n}", "allow", None)
        self.assertEqual([e["args_summary"] for e in _lines(self.vault)], ["0", "1", "2"])

    def test_newlines_in_fields_stay_on_one_line(self):
        audit.record(self.vault, "kb remove", "a\nb", "allow", "x\ny")
        raw = (Path(self.vault) / ".athena" / "audit.jsonl").read_text(encoding="utf-8")
        self.assertEqual(raw.count("\n"), 1)

    def test_sink_error_warns_and_never_raises(self):
        # .athena is a FILE: the sink can't create the log under it
        (Path(self.vault) / ".athena").write_text("x")
        err = io.StringIO()
        with redirect_stderr(err):
            self.assertFalse(audit.record(self.vault, "kb remove", "P", "allow", None))
        self.assertIn("audit", err.getvalue().lower())

    def test_rotates_past_the_cap_keeping_five(self):
        orig = audit.MAX_BYTES
        audit.MAX_BYTES = 200
        self.addCleanup(setattr, audit, "MAX_BYTES", orig)
        for n in range(60):
            audit.record(self.vault, "kb remove", f"page-{n:03d}", "allow", "r")
        d = Path(self.vault) / ".athena"
        names = sorted(p.name for p in d.iterdir())
        self.assertEqual(names, ["audit.1.jsonl", "audit.2.jsonl", "audit.3.jsonl",
                                 "audit.4.jsonl", "audit.5.jsonl", "audit.jsonl"])
        newest = _lines(self.vault)[-1]["args_summary"]
        self.assertEqual(newest, "page-059")
        self.assertLessEqual((d / "audit.jsonl").stat().st_size, 200 + 300)


class Hooks(unittest.TestCase):
    """The choke points: every destructive path goes through snapshot.py."""

    def setUp(self):
        self.vault = tempfile.mkdtemp()
        self.page = Path(self.vault) / "wiki" / "p.md"
        self.page.parent.mkdir(parents=True)
        self.page.write_text("x")

    def test_trash_is_logged_with_reason_and_counts(self):
        snapshot.trash_files(self.vault, [str(self.page)], operation="remove",
                             description="kb remove P", reason="--yes")
        (e,) = _lines(self.vault)
        self.assertEqual((e["tool"], e["args_summary"], e["decision"], e["reason"]),
                         ("kb remove", "kb remove P", "allow", "--yes"))
        self.assertEqual(e["files"], 1)
        self.assertTrue(e["batch"].startswith(".kb-trash/"))

    def test_snapshot_without_reason_still_logged(self):
        snapshot.snapshot_files(self.vault, [], [str(self.page)], operation="move", description="kb move")
        (e,) = _lines(self.vault)
        self.assertEqual((e["tool"], e["decision"]), ("kb move", "allow"))
        self.assertTrue(e["reason"])

    def test_undo_and_purge_are_logged(self):
        snapshot.trash_files(self.vault, [str(self.page)], operation="remove", description="kb remove P")
        ok, _ = snapshot.restore_last(self.vault)
        self.assertTrue(ok)
        snapshot.purge_old(self.vault, 30)
        tools = [e["tool"] for e in _lines(self.vault)]
        self.assertEqual(tools, ["kb remove", "kb undo", "kb purge"])
        self.assertIn("kb remove P", _lines(self.vault)[1]["args_summary"])

    def test_a_failing_sink_does_not_block_the_trash(self):
        (Path(self.vault) / ".athena").write_text("x")
        with redirect_stderr(io.StringIO()):
            batch = snapshot.trash_files(self.vault, [str(self.page)], operation="remove", description="d")
        self.assertFalse(self.page.exists())
        self.assertTrue(os.path.isdir(batch))



class MixedTrash(unittest.TestCase):
    """The plugin used to write its own bundle shape: .kb-trash/
    <UTC YYYYMMDDTHHMMSS>_kb-<verb>/<vault-relative files>, no manifest. The
    CLI skipped those (#143's name filter), and its local-time batch names
    sort after them on the same day ('_' > 'T'), so "the latest" was wrong
    in both directions. Both shapes are now ordered by real time."""

    def setUp(self):
        self.vault = tempfile.mkdtemp()
        self.trash = Path(self.vault) / ".kb-trash"

    def py_batch(self, epoch, rel, op="remove"):
        name = time.strftime("%Y%m%d_%H%M%S", time.localtime(epoch)) + f"_000_{op}"
        b = self.trash / name
        (b / "deleted" / rel).parent.mkdir(parents=True)
        (b / "deleted" / rel).write_text("py")
        (b / "manifest.json").write_text(json.dumps({
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(epoch)),
            "operation": op, "description": f"kb {op} {rel}", "deleted": [rel],
            "modified": [], "created": [], "status": "complete"}))
        return name

    def js_bundle(self, epoch, rel, verb="remove"):
        name = time.strftime("%Y%m%dT%H%M%S", time.gmtime(epoch)) + f"_kb-{verb}"
        f = self.trash / name / rel
        f.parent.mkdir(parents=True)
        f.write_text("js")
        return name

    def test_newer_plugin_bundle_is_the_one_undone(self):
        now = time.time()
        self.py_batch(now - 3600, "wiki/old.md")
        js = self.js_bundle(now - 60, "wiki/format/webpages/New.md")
        ok, msg = snapshot.restore_last(self.vault)
        self.assertTrue(ok, msg)
        self.assertEqual((Path(self.vault) / "wiki/format/webpages/New.md").read_text(), "js")
        self.assertFalse((Path(self.vault) / "wiki/old.md").exists(), "the older batch stays in trash")
        self.assertFalse((self.trash / js).exists())
        self.assertFalse((Path(self.vault) / "manifest.json").exists())

    def test_newer_python_batch_beats_an_older_plugin_bundle_same_day(self):
        now = time.time()
        self.js_bundle(now - 600, "wiki/js.md")
        self.py_batch(now - 60, "wiki/py.md")
        ok, msg = snapshot.restore_last(self.vault)
        self.assertTrue(ok, msg)
        self.assertTrue((Path(self.vault) / "wiki/py.md").exists())
        self.assertFalse((Path(self.vault) / "wiki/js.md").exists())

    def test_list_trash_orders_both_shapes_by_time(self):
        now = time.time()
        a = self.py_batch(now - 900, "wiki/a.md")
        b = self.js_bundle(now - 600, "wiki/b.md")
        c = self.py_batch(now - 300, "wiki/c.md")
        d = self.js_bundle(now - 60, "wiki/d.md")
        names = [n for n, _ in snapshot.list_trash(self.vault)]
        self.assertEqual(names, [a, b, c, d])
        self.assertIn("kb-remove", snapshot.list_trash(self.vault)[-1][1]["description"])

    def test_purge_ages_plugin_bundles_by_their_name(self):
        now = time.time()
        old = self.js_bundle(now - 40 * 86400, "wiki/old.md")
        new = self.js_bundle(now - 86400, "wiki/new.md")
        self.assertEqual(snapshot.purge_old(self.vault, 30), 1)
        self.assertFalse((self.trash / old).exists())
        self.assertTrue((self.trash / new).exists())

    def test_stray_dirs_still_ignored(self):
        (self.trash / "wiki").mkdir(parents=True)
        (self.trash / "20260517T223023_retroactive-dead-sweep").mkdir()
        self.assertEqual(snapshot.list_trash(self.vault), [])
        ok, _ = snapshot.restore_last(self.vault)
        self.assertFalse(ok)


class Commands(unittest.TestCase):
    """End to end through bin/kb: the issue's acceptance, and a declined
    prompt logged as deny."""

    def kb(self, vault, *args, stdin=""):
        # as test_kb_parity does: bin/ copied into the vault, run from there
        return subprocess.run([sys.executable, os.path.join(vault, "bin", "kb"), *args], cwd=vault,
                              input=stdin, capture_output=True, text=True)

    def make_vault(self):
        import shutil
        v = tempfile.mkdtemp(prefix="kb-audit-")
        shutil.copytree(ROOT / "bin", os.path.join(v, "bin"))
        for d in ("wiki/format/webpages", "raw/webpages", "inbox"):
            os.makedirs(os.path.join(v, d))
        Path(v, "CLAUDE.md").write_text("# test vault\n")
        Path(v, "RULES.md").write_text("# RULES\n")
        Path(v, "wiki/format/webpages/Some Page.md").write_text(
            '---\ntitle: "Some Page"\nraw_path: "raw/webpages/some.md"\nurl: "https://e.com/a"\n---\nbody\n')
        Path(v, "raw/webpages/some.md").write_text("raw")
        return v

    def test_remove_confirmed_then_declined(self):
        v = self.make_vault()
        r = self.kb(v, "remove", "Some Page", stdin="n\n")
        self.assertIn("Cancelled", r.stdout, r.stdout + r.stderr)
        r = self.kb(v, "remove", "Some Page", stdin="y\n")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        es = _lines(v)
        self.assertEqual([(e["tool"], e["decision"], e["reason"]) for e in es],
                         [("kb remove", "deny", "user-cancelled"),
                          ("kb remove", "allow", "user-confirmed")])
        self.assertIn("Some Page", es[1]["args_summary"])

    def test_remove_yes_flag_is_the_reason(self):
        v = self.make_vault()
        self.assertEqual(self.kb(v, "remove", "Some Page", "--yes").returncode, 0)
        self.assertEqual(_lines(v)[-1]["reason"], "--yes")

    def test_undo_declined_is_deny(self):
        v = self.make_vault()
        self.kb(v, "remove", "Some Page", "--yes")
        self.kb(v, "undo", stdin="n\n")
        self.assertEqual([(e["tool"], e["decision"]) for e in _lines(v)][-1], ("kb undo", "deny"))


    def test_issue_acceptance_audit_tail_after_remove(self):
        # #180 acceptance: kb remove + Proceed, then `kb audit tail 1`
        v = self.make_vault()
        self.kb(v, "remove", "Some Page", stdin="y\n")
        r = self.kb(v, "audit", "tail", "1")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        lines = [l for l in r.stdout.splitlines() if l.strip()]
        self.assertEqual(len(lines), 1, r.stdout)
        for want in ("Some Page", "kb remove", "allow", "user-confirmed"):
            self.assertIn(want, lines[0])

    def test_tail_default_and_count(self):
        v = self.make_vault()
        for n in range(25):
            audit.record(v, "kb purge", f"item-{n:02d}", "allow", None)
        out = self.kb(v, "audit", "tail").stdout.splitlines()
        self.assertEqual(len(out), 20)
        self.assertIn("item-24", out[-1])
        self.assertIn("item-05", out[0])
        self.assertEqual(len(self.kb(v, "audit", "tail", "3").stdout.splitlines()), 3)

    def test_tail_reaches_into_rotated_files(self):
        v = self.make_vault()
        d = Path(v, ".athena")
        d.mkdir()
        (d / "audit.2.jsonl").write_text(json.dumps({"ts": 1, "tool": "kb remove", "args_summary": "oldest",
                                                     "decision": "allow", "reason": None, "source": "cli"}) + "\n")
        (d / "audit.1.jsonl").write_text(json.dumps({"ts": 2, "tool": "kb remove", "args_summary": "middle",
                                                     "decision": "allow", "reason": None, "source": "cli"}) + "\n")
        audit.record(v, "kb undo", "newest", "allow", None)
        out = self.kb(v, "audit", "tail", "3").stdout.splitlines()
        self.assertEqual([("oldest" in l, "middle" in l, "newest" in l) for l in out],
                         [(True, False, False), (False, True, False), (False, False, True)])

    def test_grep_is_case_insensitive_across_fields_and_files(self):
        v = self.make_vault()
        audit.record(v, "kb remove", "Transformers Page", "allow", "user-confirmed")
        audit.record(v, "kb rename", "Other", "deny", "user-cancelled")
        r = self.kb(v, "audit", "grep", "transformers")
        self.assertEqual(r.returncode, 0)
        self.assertEqual(len(r.stdout.splitlines()), 1)
        self.assertIn("Transformers Page", r.stdout)
        self.assertIn("Other", self.kb(v, "audit", "grep", "DENY").stdout)
        none = self.kb(v, "audit", "grep", "zzz-nothing")
        self.assertEqual(none.returncode, 1, "no match exits 1, like grep")

    def test_empty_log_and_usage(self):
        v = self.make_vault()
        r = self.kb(v, "audit", "tail")
        self.assertEqual(r.returncode, 0)
        self.assertIn("No audit entries yet", r.stdout)
        self.assertEqual(self.kb(v, "audit").returncode, 1)
        self.assertEqual(self.kb(v, "audit", "grep").returncode, 1)
        self.assertEqual(self.kb(v, "audit", "tail", "x").returncode, 1)

    def test_unreadable_line_is_shown_not_dropped(self):
        v = self.make_vault()
        audit.record(v, "kb remove", "P", "allow", None)
        with open(audit.audit_path(v), "a", encoding="utf-8") as fh:
            fh.write("{not json\n")
        out = self.kb(v, "audit", "tail").stdout
        self.assertIn("unreadable", out)

    def test_record_is_how_the_plugin_logs_a_declined_chat_prompt(self):
        v = self.make_vault()
        r = subprocess.run([sys.executable, os.path.join(v, "bin", "kb"), "audit", "record",
                            "kb remove", "Some Page", "deny", "user-cancelled"], cwd=v,
                           capture_output=True, text=True, env={**os.environ, "ATHENA_AUDIT_SOURCE": "plugin"})
        self.assertEqual(r.returncode, 0, r.stderr)
        e = _lines(v)[-1]
        self.assertEqual((e["tool"], e["decision"], e["reason"], e["source"]),
                         ("kb remove", "deny", "user-cancelled", "plugin"))
        bad = self.kb(v, "audit", "record", "kb remove", "P", "maybe", "x")
        self.assertEqual(bad.returncode, 1, "decision is allow or deny only")


if __name__ == "__main__":
    unittest.main()
