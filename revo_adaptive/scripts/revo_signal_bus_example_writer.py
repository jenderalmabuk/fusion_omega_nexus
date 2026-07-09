#!/usr/bin/env python3
from __future__ import annotations
import json
import time
from pathlib import Path

PATH = Path("user_data/revo_alpha/runtime/revo_signal_bus.json")
PATH.parent.mkdir(parents=True, exist_ok=True)
now = time.time()
payload = {
    "BTC/USDT:USDT": {
        "direction": "LONG",
        "score": 72,
        "confidence": 0.61,
        "regime": "TRENDING",
        "entry_family": "TRENDING_RUNNER",
        "quadrant": "BULLISH_MIXED",
        "btc_weight": 1.0,
        "smc_clarity": "CLEAR",
        "risk_pct": 0.35,
        "leverage": 2.0,
        "timestamp": now,
        "data_ready": True,
        "reason": "example signal bus payload"
    }
}
PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
print(f"[OK] wrote {PATH}")
