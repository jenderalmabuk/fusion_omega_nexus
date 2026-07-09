#!/usr/bin/env python3
"""PairContextEngine phase-1 projection builder.

Default mode is dry-run candidate output:
- pair_universe_remote.json.candidate.json
- revo_execution_context.json.candidate.json

Use --apply only after reviewing the diff report.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO / "user_data") not in sys.path:
    sys.path.insert(0, str(REPO / "user_data"))

from revo_alpha.pair_context.projections import main


if __name__ == "__main__":
    raise SystemExit(main())
