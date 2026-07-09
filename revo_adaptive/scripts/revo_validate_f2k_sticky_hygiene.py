#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

def load_json(path: Path):
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runtime-dir", required=True)
    ap.add_argument("--expect-enabled", action="store_true")
    args = ap.parse_args()

    runtime = Path(args.runtime_dir)
    pairlist = load_json(runtime / "pair_universe_remote.json")
    exec_data = load_json(runtime / "revo_execution_context.json")
    report = load_json(runtime / "f2k_sticky_hygiene_latest.json")

    pairs = pairlist.get("pairs", [])
    if not isinstance(pairs, list):
        pairs = []

    exec_pairs = exec_data.get("pairs", {})
    if not isinstance(exec_pairs, dict):
        exec_pairs = {}

    failures = []

    flow_eligible = []
    sticky_no_trade_in_pairlist = []

    for pair, row in exec_pairs.items():
        if not isinstance(row, dict):
            continue
        authority = str(row.get("flow_authority", ""))
        permission = str(row.get("entry_permission", ""))
        publish_reason = str(row.get("publish_reason", ""))
        direction = str(row.get("flow_direction", ""))

        if authority == "ENTRY_ELIGIBLE" or permission == "FLOW_ELIGIBLE":
            flow_eligible.append(pair)
            if pair not in pairs:
                failures.append(f"FLOW_ELIGIBLE_MISSING:{pair}")

        if publish_reason == "STICKY_RETAINED" and direction == "NO_TRADE" and pair in pairs:
            sticky_no_trade_in_pairlist.append(pair)

    print("F2K_STICKY_HYGIENE_VALIDATION")
    print("runtime=", runtime)
    print("pairlist_count=", len(pairs))
    print("flow_eligible_count=", len(flow_eligible))
    print("flow_eligible_pairs=", flow_eligible)
    print("sticky_no_trade_in_pairlist_count=", len(sticky_no_trade_in_pairlist))
    print("sticky_no_trade_in_pairlist=", sticky_no_trade_in_pairlist)
    print("latest_report_enabled=", report.get("enabled"))
    print("latest_report_writes_pairlist=", report.get("writes_pairlist"))
    print("latest_report_drop_count=", report.get("drop_count"))

    if args.expect_enabled and sticky_no_trade_in_pairlist:
        failures.append("STICKY_NO_TRADE_STILL_IN_PAIRLIST")

    if args.expect_enabled and not report.get("enabled"):
        failures.append("REPORT_NOT_ENABLED")

    if failures:
        print("failures=", len(failures))
        for f in failures:
            print("FAIL:", f)
        print("F2K_STICKY_HYGIENE_FAIL")
        return 1

    print("failures=0")
    print("F2K_STICKY_HYGIENE_PASS")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
