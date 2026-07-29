"""Compare WebSocket shadow setups against polling production state. Read-only."""
from __future__ import annotations

import json
import os
from collections import Counter
from pathlib import Path

SHADOW = Path(os.getenv("FQ_WS_SHADOW_AUDIT", "runtime/fusion_quantum/ws_shadow.jsonl"))
STATE = Path(os.getenv("FQ_STATE_PATH", "runtime/fusion_quantum/state.json"))


def main() -> None:
    rows = [json.loads(x) for x in SHADOW.read_text().splitlines() if x.strip()]
    scans = [r for r in rows if r.get("event") == "shadow_scan"]
    errors = [r for r in rows if r.get("event") == "shadow_error"]
    ready = [r for r in scans if r.get("candle_ready")]

    shadow_ids = {sid: r["symbol"] for r in scans for sid in r["setup_ids"]}
    prod_ids = set(json.loads(STATE.read_text()).get("setups", {}))

    latency = sorted(r["scan_finished_at"] - r["ws_received_at"] for r in scans)
    waits = Counter(r.get("ready_waits", 0) for r in scans)

    print(json.dumps({
        "scans": len(scans),
        "errors": len(errors),
        "symbols": len({r["symbol"] for r in scans}),
        "candles": sorted({r["candle_start"] for r in scans}),
        "candle_ready": f"{len(ready)}/{len(scans)}",
        "not_ready_symbols": [r["symbol"] for r in scans if not r.get("candle_ready")][:20],
        "ready_waits_hist": dict(sorted(waits.items())),
        "latency_sec": {
            "min": round(latency[0], 1) if latency else None,
            "p50": round(latency[len(latency) // 2], 1) if latency else None,
            "max": round(latency[-1], 1) if latency else None,
        },
        "shadow_setups": len(shadow_ids),
        "in_production_state": sum(1 for sid in shadow_ids if sid in prod_ids),
        "shadow_only": {sid: sym for sid, sym in shadow_ids.items() if sid not in prod_ids},
    }, indent=2))


if __name__ == "__main__":
    main()
