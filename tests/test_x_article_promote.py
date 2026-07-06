import os, sys, importlib.util, importlib.machinery, unittest
LIB = os.path.join(os.path.dirname(__file__), "..", "bin", "lib")
sys.path.insert(0, LIB)

_kc_path = os.path.join(os.path.dirname(__file__), "..", "bin", "lib", "capture.py")
_spec = importlib.util.spec_from_loader(
    "kbcapture", importlib.machinery.SourceFileLoader("kbcapture", _kc_path))
kc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(kc)

ARTICLE = {"article": {"rest_id": "123", "title": "T"}}
TWEET = {"id_str": "9", "text": "hi"}


class ShouldDeepCapture(unittest.TestCase):
    def test_article_marker_enabled(self):
        self.assertTrue(kc._should_deep_capture_article(ARTICLE, True, {}))

    def test_no_marker(self):
        self.assertFalse(kc._should_deep_capture_article(ARTICLE, False, {}))

    def test_disabled_env(self):
        self.assertFalse(kc._should_deep_capture_article(
            ARTICLE, True, {"ATHENA_X_DEEPCAPTURE": "0"}))

    def test_plain_tweet_never(self):
        self.assertFalse(kc._should_deep_capture_article(TWEET, True, {}))


if __name__ == "__main__":
    unittest.main()
