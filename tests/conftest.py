"""Shared pytest fixtures for the Athena test suite."""
import os
import sys
from pathlib import Path

import pytest

# Import Athena's server package (server/athena) from THIS checkout. The
# editable install is a user-site .pth naming whichever checkout ran
# `pip install -e server`, so a run in a worktree or second clone would
# otherwise import that checkout's code. PYTHONPATH carries it into the
# kb/python3 subprocesses the suite starts (it is searched before .pth paths).
_SERVER = str(Path(__file__).resolve().parent.parent / "server")
sys.path.insert(0, _SERVER)
os.environ["PYTHONPATH"] = os.pathsep.join(
    p for p in (_SERVER, os.environ.get("PYTHONPATH")) if p)


@pytest.fixture(autouse=True)
def _hermetic_tweet_resync(monkeypatch):
    """Keep the suite hermetic: the Web-Clipper /status/ re-sync makes a live
    syndication-CDN call during ingest. Disable it by default so tests that
    happen to feed an x.com /status/ web-clipper clip (pipeline-parity, writers)
    don't hit the network. Tests that exercise the re-sync re-enable it
    explicitly and mock `fetch_tweet.fetch_tweet_json`.
    """
    monkeypatch.setenv("ATHENA_DISABLE_TWEET_RESYNC", "1")
