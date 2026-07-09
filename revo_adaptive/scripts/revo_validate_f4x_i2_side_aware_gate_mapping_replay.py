#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runtime-dir", default="/home/fusion_omega/revo_adaptive/user_data/revo_alpha/runtime/bybit")
    args = ap.parse_args()

    runtime = Path(args.runtime_dir)
    p = runtime / "F4X_I2_SIDE_AWARE_GATE_MAPPING_REPLAY_FULL.json"

    print("F4X_I2_SIDE_AWARE_GATE_MAPPING_REPLAY_VALIDATION")
    print("runtime=", runtime)
    print("exists=", p.exists())

    failures = []

    if not p.exists():
        failures.append("MISSING_FULL_JSON")
    else:
        data = json.loads(p.read_text(encoding="utf-8", errors="replace"))
        print("final_decision=", data.get("final_decision"))
        print("target_count=", data.get("target_count"))
        print("lane_counts=", data.get("lane_counts"))
        print("cvdoi_alignment_counts=", data.get("cvdoi_alignment_counts"))

        if data.get("paper_bridge") != "HOLD":
            failures.append("PAPER_BRIDGE_NOT_HOLD")
        if data.get("live") != "HOLD":
            failures.append("LIVE_NOT_HOLD")
        if data.get("risk_up") != "HOLD":
            failures.append("RISK_UP_NOT_HOLD")
        if data.get("gate_loosen") != "HOLD":
            failures.append("GATE_LOOSEN_NOT_HOLD")
        if data.get("target_count", 0) <= 0:
            failures.append("NO_TARGETS")

    print("failures=", len(failures))
    for f in failures:
        print("FAIL:" + f)

    if failures:
        return 1

    print("F4X_I2_SIDE_AWARE_GATE_MAPPING_REPLAY_VALIDATION_PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
