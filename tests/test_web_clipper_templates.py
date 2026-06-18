"""Every shipped Athena Web Clipper template must satisfy the ingest contract:
path=inbox/Clippings, source={{url}}, a non-empty title source, clipped_via in
{web-clipper, web-clipper-social}, valid schemaVersion; social templates carry
URL triggers. The X-Article template additionally targets the long-form body
selector and an /i/article/ trigger (distinct from the tweet template's /status/
trigger so the two never collide).

Run:  python3 -m pytest tests/test_web_clipper_templates.py -v
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path

_DIR = Path(__file__).resolve().parent.parent / "assets" / "web-clipper"
_TEMPLATES = [
    "athena-universal.json",
    "athena-linkedin.json",
    "athena-x.json",
    "athena-x-article.json",
]
_SOCIAL = ("athena-linkedin.json", "athena-x.json", "athena-x-article.json")


def _props(tpl: dict) -> dict:
    return {p["name"]: p["value"] for p in tpl.get("properties", [])}


class TemplateContract(unittest.TestCase):
    def _load(self, name: str) -> dict:
        return json.loads((_DIR / name).read_text(encoding="utf-8"))

    def test_all_templates_present_and_valid(self):
        for name in _TEMPLATES:
            tpl = self._load(name)
            self.assertEqual(tpl["schemaVersion"], "0.1.0", name)
            self.assertEqual(tpl["path"], "inbox/Clippings", name)
            props = _props(tpl)
            self.assertEqual(props.get("source"), "{{url}}", name)
            # title may be {{title}} or a DOM-selector (X Articles render a
            # generic <title>, so they pull the real headline via selector).
            self.assertTrue((props.get("title") or "").strip(),
                            f"{name} must set a non-empty title")
            self.assertIn(props.get("clipped_via"),
                          ("web-clipper", "web-clipper-social"), name)
            self.assertTrue((tpl.get("noteContentFormat") or "").strip(),
                            f"{name} must define a body (noteContentFormat)")

    def test_social_templates_have_triggers(self):
        for name in _SOCIAL:
            tpl = self._load(name)
            self.assertTrue(tpl.get("triggers"), f"{name} must define URL triggers")
            self.assertEqual(_props(tpl)["clipped_via"], "web-clipper-social", name)

    def test_x_article_targets_longform_body_and_article_trigger(self):
        tpl = self._load("athena-x-article.json")
        self.assertIn("longformRichTextComponent", tpl["noteContentFormat"],
                      "X-Article body must target the long-form rich-text container")
        triggers = tpl["triggers"]
        joined = " ".join(triggers)
        # Canonical /i/article/ form (prefix) ...
        self.assertIn("/i/article/", joined,
                      "X-Article must trigger on the canonical /i/article/ form")
        # ... AND the handle-based /<handle>/article/ form X actually serves
        # (e.g. x.com/FakeMaidenMaker/article/<id>) via a regex trigger.
        self.assertTrue(
            any(t.startswith("/") and "article" in t for t in triggers),
            "X-Article must also match the handle-based /<handle>/article/ form")

    def test_tweet_and_article_triggers_do_not_overlap(self):
        # The tweet template must NOT also grab /i/article/ pages, or both fire.
        x = self._load("athena-x.json")
        self.assertNotIn("/i/article/", " ".join(x["triggers"]),
                         "tweet template must not match article URLs")
        # ...and it must scope to status URLs (regex), not all of x.com.
        self.assertTrue(any("status" in t for t in x["triggers"]),
                        "tweet template must scope to /status/ URLs")


if __name__ == "__main__":
    unittest.main()
