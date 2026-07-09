#!/usr/bin/env python3
from pathlib import Path
import json
import time

runtime = Path("user_data/revo_alpha/runtime")
runtime.mkdir(parents=True, exist_ok=True)

payload = {
    "BTC/USDT:USDT": {
        "price_delta_pct": 0.42,
        "oi_delta_pct": 1.15,
        "cvd_delta": 1250000,
        "cvd_zscore": 1.1,
        "funding_rate": -0.00021,
        "funding_zscore": -0.8,
        "volume_zscore": 1.4,
        "flow_quadrant": "BULL_CONTINUATION_SHORTS_TRAPPED",
        "timestamp": time.time(),
        "data_ready": True
    }
}

(runtime / "revo_flow_context.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
print("[OK] wrote user_data/revo_alpha/runtime/revo_flow_context.json")
