#!/usr/bin/env python3
"""Lifecycle repair planner for PairContext drift reports.

Read-only planner. It writes a repair roadmap, not runtime repairs.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO / "user_data") not in sys.path:
    sys.path.insert(0, str(REPO / "user_data"))

from revo_alpha.pair_context.repair_planner import main


if __name__ == "__main__":
    raise SystemExit(main())
