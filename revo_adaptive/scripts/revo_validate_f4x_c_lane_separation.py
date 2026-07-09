#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runtime-dir", default="user_data/revo_alpha/runtime/bybit")
    args = ap.parse_args()

    runtime = Path(args.runtime_dir)
    p = runtime / "F4X_C_LANE_SEPARATION_FULL.json"

    print("F4X_C_LANE_SEPARATION_VALIDATION")
    print("runtime=", runtime)
    print("exists=", p.exists())

    if not p.exists():
        print("failures=1")
        print("FAIL:MISSING_F4X_C_FULL")
        return 1

    data = json.loads(p.read_text(encoding="utf-8", errors="replace"))

    failures = []
    if data.get("live_allowed") is not False:
        failures.append("LIVE_ALLOWED_NOT_FALSE")
    if data.get("risk_change") != "NONE":
        failures.append("RISK_CHANGE_NOT_NONE")
    if data.get("gate_loosen") != "NONE":
        failures.append("GATE_LOOSEN_NOT_NONE")

    entry_ready = data.get("entry_ready", [])
    for row in entry_ready:
        if row.get("paper_bridge_allowed") is not True:
            failures.append("ENTRY_READY_BRIDGE_FLAG_FALSE")

    print("final_decision=", data.get("final_decision"))
    print("candidate_count=", data.get("candidate_count"))
    print("lane_counts=", data.get("lane_counts"))
    print("entry_ready_count=", len(entry_ready))
    print("paper_bridge_allowed=", data.get("paper_bridge_allowed"))
    print("failures=", len(failures))

    for f in failures:
        print("FAIL:" + f)

    if failures:
        return 1

    print("F4X_C_LANE_SEPARATION_VALIDATION_PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
