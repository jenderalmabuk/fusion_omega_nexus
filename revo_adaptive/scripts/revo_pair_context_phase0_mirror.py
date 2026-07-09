#!/usr/bin/env python3
"""Phase-0 PairContextEngine passive mirror.

This script only reads existing runtime files and writes
pair_context_store.sqlite. It does not write legacy runtime projections.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO / "user_data") not in sys.path:
    sys.path.insert(0, str(REPO / "user_data"))

from revo_alpha.pair_context.cli import main


if __name__ == "__main__":
    raise SystemExit(main())
