#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path


def load_json(path: Path):
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8", errors="replace"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runtime-dir", default="user_data/revo_alpha/runtime/bybit")
    ap.add_argument("--expect-applied", action="store_true")
    ap.add_argument("--min-active-pairs", type=int, default=24)
    args = ap.parse_args()

    runtime = Path(args.runtime_dir)
    proposal_path = runtime / "F4X_D2_SAFE_MERGE_ACTIVE_PAIRLIST_PROPOSAL.json"
    pairlist_path = runtime / "pair_universe_remote.json"

    proposal = load_json(proposal_path)
    pairlist = load_json(pairlist_path)

    failures = []

    print("F4X_D2_SAFE_MERGE_PAIRLIST_VALIDATION")
    print("runtime=", runtime)
    print("proposal_exists=", proposal_path.exists())
    print("pairlist_exists=", pairlist_path.exists())
    print("decision=", proposal.get("decision"))
    print("apply_requested=", proposal.get("apply_requested"))
    print("apply_performed=", proposal.get("apply_performed"))
    print("safe_to_apply=", proposal.get("safe_to_apply"))
    print("final_pair_count=", proposal.get("final_pair_count"))
    print("promoted_in_final_count=", proposal.get("promoted_in_final_count"))

    if not proposal_path.exists():
        failures.append("MISSING_PROPOSAL")
    if proposal.get("live") != "HOLD":
        failures.append("LIVE_NOT_HOLD")
    if proposal.get("risk_up") != "HOLD":
        failures.append("RISK_UP_NOT_HOLD")
    if proposal.get("gate_loosen") != "HOLD":
        failures.append("GATE_LOOSEN_NOT_HOLD")

    if proposal.get("final_pair_count", 0) < args.min_active_pairs and proposal.get("safe_to_apply"):
        failures.append("SAFE_TO_APPLY_TRUE_BELOW_MIN")

    if args.expect_applied:
        pairs = pairlist.get("pairs", []) if isinstance(pairlist, dict) else []
        print("pairlist_pair_count=", len(pairs))
        if len(pairs) < args.min_active_pairs:
            failures.append("PAIRLIST_BELOW_MIN_AFTER_APPLY")
        for p in proposal.get("promoted_in_final", []):
            if p not in pairs:
                failures.append("PROMOTED_PAIR_NOT_IN_PAIRLIST:" + str(p))
        if pairlist.get("f4x_d2_safe_merge_enabled") is not True:
            failures.append("F4X_D2_FLAG_NOT_SET")

    print("failures=", len(failures))
    for f in failures:
        print("FAIL:" + f)

    if failures:
        return 1

    print("F4X_D2_SAFE_MERGE_PAIRLIST_VALIDATION_PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
