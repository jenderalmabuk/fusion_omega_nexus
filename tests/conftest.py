"""Shared pytest fixtures. Puts fusionnew/ and repo root on sys.path so
`backtest`, `clean_core`, `signals` and `bots` import exactly as in production."""
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FUSION = os.path.join(REPO_ROOT, "fusionnew")
for p in (REPO_ROOT, FUSION):
    if p not in sys.path:
        sys.path.insert(0, p)

os.environ.setdefault("LOG_DIR", "/tmp/bt_logs")
# Isolate engine state written during tests (must be set BEFORE engine import).
os.environ.setdefault("STATE_DIR", "/tmp/test_engine_state")

