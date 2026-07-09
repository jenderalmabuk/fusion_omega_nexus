#!/usr/bin/env python3
"""PairContextEngine drift analyzer.

Read-only analyzer for legacy runtime state and pair_context_store.sqlite.
It writes PAIR_CONTEXT_DRIFT_REPORT.json and compact telemetry report.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO / "user_data") not in sys.path:
    sys.path.insert(0, str(REPO / "user_data"))

from revo_alpha.pair_context.drift import main


if __name__ == "__main__":
    raise SystemExit(main())
