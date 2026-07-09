#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path


def load(path: Path):
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8", errors="replace"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runtime-dir", default="/home/fusion_omega/revo_adaptive/user_data/revo_alpha/runtime/bybit")
    args = ap.parse_args()

    runtime = Path(args.runtime_dir)
    j = load(runtime / "F4X_J_SIDE_AWARE_MAPPING_SHADOW_CLASSIFIER_FULL.json")
    k = load(runtime / "F4X_K_PAPER_BRIDGE_INTENTS_FULL.json")
    active = load(runtime / "F4X_K_PAPER_BRIDGE_ACTIVE_SIGNAL.json")

    failures = []

    print("F4X_L2_JK_INTENT_LAYER_VALIDATION")
    print("j_exists=", bool(j))
    print("k_exists=", bool(k))
    print("active_exists=", bool(active))
    print("j_rows=", j.get("row_count"))
    print("k_intent_count=", k.get("intent_count"))
    print("k_counts=", k.get("intent_action_counts"))
    print("active_has_order_intent=", active.get("has_order_intent"))

    for name, data in [("J", j), ("K", k)]:
        if not data:
            failures.append(f"{name}_MISSING")
            continue
        if data.get("live") != "HOLD":
            failures.append(f"{name}_LIVE_NOT_HOLD")
        if data.get("risk_up") != "HOLD":
            failures.append(f"{name}_RISK_UP_NOT_HOLD")
        if data.get("gate_loosen") != "HOLD":
            failures.append(f"{name}_GATE_LOOSEN_NOT_HOLD")

    if not active:
        failures.append("ACTIVE_SIGNAL_MISSING")

    print("failures=", len(failures))
    for f in failures:
        print("FAIL:" + f)

    if failures:
        return 1

    print("F4X_L2_JK_INTENT_LAYER_VALIDATION_PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
