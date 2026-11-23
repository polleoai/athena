"""bin/lib/search.py pins the context of its Ollama embed calls (#240).

With no `num_ctx`, Ollama on a 64 GB machine loads a model at its maximum
context (voiceagent: qwen3:4b at 262144 / 41 GB), colliding with runs that
need the box alone. Offline: the HTTP connection is a stub; no Ollama runs.
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest import mock

LIB = Path(__file__).resolve().parent.parent / "bin" / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

import search  # noqa: E402


class _Resp:
    status = 200

    def read(self):
        return json.dumps({"embeddings": [[0.1, 0.2]]}).encode()


class _Conn:
    sent: list = []

    def __init__(self, host, port, timeout=None):
        _Conn.target = (host, port)

    def request(self, method, path, body=None, headers=None):
        _Conn.sent.append((method, path, json.loads(body) if body else None))

    def getresponse(self):
        return _Resp()

    def close(self):
        pass


class OllamaEmbedContext(unittest.TestCase):
    def setUp(self):
        _Conn.sent = []

    def _embed(self, text):
        with mock.patch("http.client.HTTPConnection", _Conn):
            return search._ollama_embed_single(text)

    def test_embed_request_pins_num_ctx_and_truncates(self):
        self.assertEqual(self._embed("hello"), [0.1, 0.2])
        method, path, body = _Conn.sent[-1]
        self.assertEqual((method, path), ("POST", "/api/embed"))
        self.assertEqual(body["options"]["num_ctx"], search.OLLAMA_NUM_CTX)
        self.assertIs(body["truncate"], True)
        self.assertEqual(_Conn.target, ("127.0.0.1", 11434))

    def test_pinned_context_covers_athenas_largest_input(self):
        # A 500-word chunk or title + 2000 chars: well under the pin even at
        # a pessimistic 2 tokens per word / 1 token per 2 chars.
        self.assertGreaterEqual(search.OLLAMA_NUM_CTX, 500 * 2)
        self.assertGreaterEqual(search.OLLAMA_NUM_CTX, 2000 // 2 + 200)
        self.assertLessEqual(search.OLLAMA_NUM_CTX, 8192, "no larger than nomic-embed-text's maximum")


if __name__ == "__main__":
    unittest.main()
