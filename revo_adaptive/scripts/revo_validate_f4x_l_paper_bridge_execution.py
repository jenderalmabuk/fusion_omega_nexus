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
    l = load(runtime / "F4X_L_PAPER_BRIDGE_EXECUTION_FULL.json")
    o = load(runtime / "F4X_L_PAPER_TRADE_OUTCOME_AUDIT_FULL.json")

    failures = []

    print("F4X_L_PAPER_BRIDGE_EXECUTION_VALIDATION")
    print("runtime=", runtime)
    print("execution_exists=", bool(l))
    print("outcome_exists=", bool(o))
    print("decision=", l.get("decision"))
    print("dry_run_verified=", l.get("dry_run_verified"))
    print("would_order_intent_count=", l.get("would_order_intent_count"))
    print("orders=", len(l.get("orders", [])) if isinstance(l.get("orders"), list) else 0)
    print("blocked=", len(l.get("blocked", [])) if isinstance(l.get("blocked"), list) else 0)
    print("errors=", len(l.get("errors", [])) if isinstance(l.get("errors"), list) else 0)

    if l and l.get("live") != "HOLD":
        failures.append("LIVE_NOT_HOLD")
    if l and l.get("risk_up") != "HOLD":
        failures.append("RISK_UP_NOT_HOLD")
    if l and l.get("gate_loosen") != "HOLD":
        failures.append("GATE_LOOSEN_NOT_HOLD")
    if o and o.get("live") != "HOLD":
        failures.append("OUTCOME_LIVE_NOT_HOLD")

    print("failures=", len(failures))
    for f in failures:
        print("FAIL:" + f)

    if failures:
        return 1

    print("F4X_L_PAPER_BRIDGE_EXECUTION_VALIDATION_PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
