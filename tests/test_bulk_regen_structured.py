"""#181, Python side: the bulk summary regen asks `claude -p --json-schema`
for the digest and reads the validated `structured_output`; the old regex
parse of the reply text is only the fallback when none came back.

The result-event shape was witnessed with Claude Code 2.1.326 (2026-11-19):
`structured_output` holds the object, `result` the same JSON as text. No
claude process runs here: run_claude_with_idle_timeout is replaced.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import _athena_timeout as at  # noqa: E402


def _load_regen():
    spec = importlib.util.spec_from_file_location(
        "bulk_regen_summaries", ROOT / "scripts" / "bulk-llm-regen-summaries.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _stream(*events) -> str:
    return "\n".join(json.dumps(e) for e in events) + "\n"


DIGEST = {"summary": "Summary:  Water boils at\n100 C at sea level.", "body": "## Key Findings\n- 100 C"}


class ExtractStructured(unittest.TestCase):
    def test_reads_the_result_events_object(self):
        raw = _stream({"type": "assistant", "message": {"content": []}},
                      {"type": "result", "result": json.dumps(DIGEST), "structured_output": DIGEST})
        self.assertEqual(at.extract_structured_output(raw), DIGEST)

    def test_none_without_an_object(self):
        for raw in ("", "not json\n", _stream({"type": "result", "result": "x"}),
                    _stream({"type": "result", "structured_output": "a string"}),
                    _stream({"type": "result", "structured_output": [1]})):
            with self.subTest(raw=raw[:40]):
                self.assertIsNone(at.extract_structured_output(raw))


class DigestCall(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.regen = _load_regen()

    def call(self, raw_out, rc=0):
        seen = {}

        def fake(cmd, idle_s):
            seen["cmd"] = cmd
            return rc, raw_out, ""
        orig = self.regen.run_claude_with_idle_timeout
        self.regen.run_claude_with_idle_timeout = fake
        try:
            return self.regen.call_claude_for_digest("T", "webpage", "", "body"), seen["cmd"]
        finally:
            self.regen.run_claude_with_idle_timeout = orig

    def test_passes_the_schema_and_uses_structured_output(self):
        out, cmd = self.call(_stream({"type": "result", "result": "ignored", "structured_output": DIGEST}))
        i = cmd.index("--json-schema")
        self.assertEqual(json.loads(cmd[i + 1]), self.regen.DIGEST_JSON_SCHEMA)
        self.assertLess(i, len(cmd) - 1, "the flag precedes the prompt (the last argument)")
        # normalised as before: newline collapsed, "Summary:" preface stripped
        self.assertEqual(out["summary"], "Water boils at 100 C at sea level.")
        self.assertEqual(out["body"], DIGEST["body"])

    def test_text_fallback_when_no_structured_output(self):
        text = "```json\n" + json.dumps(DIGEST) + "\n```"
        with _quiet_stderr() as err:
            out, _ = self.call(_stream({"type": "result", "result": text}))
        self.assertEqual(out["body"], DIGEST["body"])
        self.assertIn("no structured_output", err.getvalue(), "the fallback says so")

    def test_nothing_usable_is_empty(self):
        with _quiet_stderr():
            out, _ = self.call(_stream({"type": "result", "result": "sorry, no"}))
        self.assertEqual(out, {})

    def test_nonzero_exit_raises(self):
        with self.assertRaises(RuntimeError):
            self.call("", rc=1)



# #179: a cost ceiling for the sweep. Shapes witnessed with Claude Code
# 2.1.326 (2026-11-19): every result event carries total_cost_usd; with
# --max-budget-usd exceeded the CLI exits 1 with subtype
# error_max_budget_usd and still reports what it spent (it checks after the
# turn, so the spend can pass the cap).
def _result(cost, subtype="success", **kw):
    ev = {"type": "result", "subtype": subtype, "is_error": subtype != "success",
          "result": json.dumps(DIGEST), "structured_output": DIGEST, **kw}
    if cost is not None:
        ev["total_cost_usd"] = cost
    return _stream(ev)


class Budget(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.regen = _load_regen()

    def call(self, budget, raw_out, rc=0):
        seen = {}

        def fake(cmd, idle_s):
            seen["cmd"] = cmd
            return rc, raw_out, ""
        orig = self.regen.run_claude_with_idle_timeout
        self.regen.run_claude_with_idle_timeout = fake
        try:
            return self.regen.call_claude_for_digest("T", "webpage", "", "body", budget=budget), seen["cmd"]
        finally:
            self.regen.run_claude_with_idle_timeout = orig

    def test_each_call_is_capped_at_what_is_left_and_charged(self):
        b = self.regen.Budget(1.0)
        b.spent = 0.25
        out, cmd = self.call(b, _result(0.1))
        self.assertEqual(out["body"], DIGEST["body"])
        i = cmd.index("--max-budget-usd")
        self.assertAlmostEqual(float(cmd[i + 1]), 0.75)
        self.assertAlmostEqual(b.spent, 0.35)

    def test_cli_budget_stop_raises_and_still_charges(self):
        b = self.regen.Budget(0.5)
        with self.assertRaises(self.regen.BudgetExceeded):
            self.call(b, _result(0.62, subtype="error_max_budget_usd",
                                 errors=["Reached maximum budget ($0.5)"]), rc=1)
        self.assertAlmostEqual(b.spent, 0.62)

    def test_unreadable_cost_stops_the_sweep(self):
        # can't tell what it cost = can't enforce the cap: stop, never guess 0
        b = self.regen.Budget(5.0)
        with self.assertRaises(self.regen.BudgetExceeded) as cm:
            self.call(b, _result(None))
        self.assertIn("cost", str(cm.exception))

    def test_spent_budget_refuses_before_calling(self):
        b = self.regen.Budget(1.0)
        b.spent = 1.0
        with self.assertRaises(self.regen.BudgetExceeded):
            self.regen.call_claude_for_digest("T", "webpage", "", "body", budget=b)

    def test_default_cap_and_flag(self):
        src = (ROOT / "scripts" / "bulk-llm-regen-summaries.py").read_text(encoding="utf-8")
        self.assertIn('"--max-usd"', src)
        self.assertEqual(self.regen.DEFAULT_MAX_USD, 10.0)

    def test_cap_must_be_positive(self):
        with self.assertRaises(ValueError):
            self.regen.Budget(0)


class BudgetSweep(unittest.TestCase):
    """main() stops cleanly at the cap: the state file keeps what finished,
    the remaining count and a resume hint are printed, exit 3."""

    def test_sweep_stops_at_the_cap_with_a_resume_hint(self):
        import contextlib
        import io
        import tempfile
        regen = _load_regen()
        tmp = Path(tempfile.mkdtemp())
        wiki = tmp / "wiki" / "format" / "webpages"
        wiki.mkdir(parents=True)
        for n in range(4):
            (wiki / f"p{n}.md").write_text(f"---\ntitle: P{n}\nsummary: old\n---\nbody {n}\n", encoding="utf-8")
        regen.VAULT, regen.WIKI_FORMAT = tmp, tmp / "wiki" / "format"
        regen.STATE_FILE = tmp / "state.json"
        regen.get_raw_body = lambda path, fm: "body"
        regen.quality_check_summary = lambda s: (True, "")
        regen.quality_check_body = lambda s: (True, "")
        regen.update_wiki_digest = lambda *a, **k: True
        calls = []

        def fake(cmd, idle_s):
            calls.append(cmd)
            return 0, _result(0.4), ""
        regen.run_claude_with_idle_timeout = fake
        out = io.StringIO()
        old = sys.argv
        sys.argv = ["regen", "--max-usd", "1"]
        try:
            with contextlib.redirect_stdout(out), _quiet_stderr():
                rc = regen.main()
        finally:
            sys.argv = old
        self.assertEqual(rc, 3, out.getvalue())
        self.assertEqual(len(calls), 3, "0.4 + 0.4 < 1 allows a third call; then 1.2 >= 1 stops")
        state = json.loads(regen.STATE_FILE.read_text())
        self.assertEqual(len(state["completed"]), 3)
        text = out.getvalue()
        self.assertIn("1 page(s) left", text)
        self.assertIn("--max-usd", text, "the resume hint names the flag")



class TagsSweepBudget(unittest.TestCase):
    """The sibling sweep, bulk-llm-regen-tags.py, carries the same cap."""

    def load(self):
        spec = importlib.util.spec_from_file_location(
            "bulk_regen_tags", ROOT / "scripts" / "bulk-llm-regen-tags.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def run_main(self, tags, argv, cost=0.4, subtype="success", rc=0):
        import contextlib
        import io
        import tempfile
        tmp = Path(tempfile.mkdtemp())
        wiki = tmp / "wiki" / "format" / "webpages"
        wiki.mkdir(parents=True)
        for n in range(4):
            (wiki / f"p{n}.md").write_text(f"---\ntitle: P{n}\ntags: []\n---\nbody {n}\n", encoding="utf-8")
        tags.VAULT, tags.WIKI_FORMAT = tmp, tmp / "wiki" / "format"
        tags.update_tags_field = lambda *a, **k: True
        calls = []

        def fake(cmd, idle_s):
            calls.append(cmd)
            ev = {"type": "result", "subtype": subtype, "result": "llm, ai-agents, tool, security"}
            if cost is not None:
                ev["total_cost_usd"] = cost
            return rc, _stream({"type": "assistant", "message": {"content": [
                {"type": "text", "text": "llm, ai-agents, tool, security"}]}}, ev), ""
        tags.run_claude_with_idle_timeout = fake
        out = io.StringIO()
        old = sys.argv
        sys.argv = ["tags", *argv]
        try:
            with contextlib.redirect_stdout(out), _quiet_stderr():
                code = tags.main()
        finally:
            sys.argv = old
        return code, calls, out.getvalue()

    def test_stops_at_the_cap(self):
        code, calls, out = self.run_main(self.load(), ["--max-usd", "1"])
        self.assertEqual(code, 3, out)
        self.assertEqual(len(calls), 3)
        self.assertIn("1 page(s) left", out)
        i = calls[0].index("--max-budget-usd")
        self.assertAlmostEqual(float(calls[0][i + 1]), 1.0)
        self.assertEqual(calls[0][-1][:11], "PAGE TITLE:", "the prompt stays last")

    def test_unreadable_cost_stops(self):
        code, calls, out = self.run_main(self.load(), [], cost=None)
        self.assertEqual(code, 3, out)
        self.assertEqual(len(calls), 1)

    def test_cli_budget_stop_is_not_retried(self):
        code, calls, out = self.run_main(self.load(), ["--max-usd", "5"], cost=5.2,
                                         subtype="error_max_budget_usd", rc=1)
        self.assertEqual(code, 3, out)
        self.assertEqual(len(calls), 1, "a budget stop is not a retryable call failure")

    def test_under_the_cap_runs_everything(self):
        code, calls, out = self.run_main(self.load(), ["--max-usd", "100"], cost=0.01)
        self.assertEqual(code, 0, out)
        self.assertEqual(len(calls), 4)


class _quiet_stderr:
    def __enter__(self):
        import io
        self._old, sys.stderr = sys.stderr, io.StringIO()
        return sys.stderr

    def __exit__(self, *a):
        sys.stderr = self._old


if __name__ == "__main__":
    unittest.main()
