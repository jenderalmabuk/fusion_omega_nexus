#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runtime-dir", default="/home/fusion_omega/revo_adaptive/user_data/revo_alpha/runtime/bybit")
    ap.add_argument("--max-age-sec", type=float, default=420)
    args = ap.parse_args()

    runtime = Path(args.runtime_dir)
    js = runtime / "revo_flow_context_collector.json"
    csv = runtime / "revo_flow_context_collector.csv"
    hb = runtime / "BYBIT_FLOW_COLLECTOR_HEARTBEAT_COMPACT.txt"

    failures = []

    print("F2G_BYBIT_FLOW_COLLECTOR_AUDIT")
    print("runtime=", runtime)

    for p in [js, csv, hb]:
        print()
        print("---", p.name, "---")
        if not p.exists():
            print("exists=False")
            failures.append(f"MISSING:{p.name}")
            continue
        age = time.time() - p.stat().st_mtime
        print("exists=True age_sec=", round(age, 1), "size=", p.stat().st_size)
        if age > args.max_age_sec:
            failures.append(f"STALE:{p.name}:{age:.1f}")

    rows = []
    if js.exists():
        try:
            data = json.loads(js.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                rows = [v for v in data.values() if isinstance(v, dict)]
            elif isinstance(data, list):
                rows = [v for v in data if isinstance(v, dict)]
            print("rows=", len(rows))
            source_counts = {}
            for r in rows:
                source_counts[str(r.get("source", "UNKNOWN"))] = source_counts.get(str(r.get("source", "UNKNOWN")), 0) + 1
            print("source_counts=", source_counts)
            print("sample_pairs=", [r.get("pair") for r in rows[:10]])
            if not rows:
                failures.append("ROWS_ZERO")
            if any("BINANCE" in str(r.get("source", "")).upper() for r in rows):
                failures.append("BINANCE_SOURCE_FOUND")
            required = ["price_delta_pct_15m", "price_delta_pct_1h", "oi_delta_pct_15m", "oi_delta_pct_1h", "cvd_zscore_15m"]
            missing_required = []
            for k in required:
                if any(k not in r for r in rows[:5]):
                    missing_required.append(k)
            print("missing_required_in_sample=", missing_required)
            if missing_required:
                failures.append("MISSING_REQUIRED_FIELDS:" + ",".join(missing_required))
        except Exception as e:
            failures.append(f"JSON_READ_ERROR:{e}")
            print("json_read_error=", e)

    if failures:
        print()
        print("failures=", len(failures))
        for f in failures:
            print("FAIL:", f)
        print("F2G_BYBIT_FLOW_COLLECTOR_FAIL")
        return 1

    print()
    print("failures=0")
    print("F2G_BYBIT_FLOW_COLLECTOR_PASS")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
