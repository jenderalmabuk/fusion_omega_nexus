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
    args = ap.parse_args()

    runtime = Path(args.runtime_dir)
    proposal_path = runtime / "F4X_D_ACTIVE_OBSERVATION_PAIRLIST_PROPOSAL.json"
    pairlist_path = runtime / "pair_universe_remote.json"

    proposal = load_json(proposal_path)
    pairlist = load_json(pairlist_path)

    failures = []

    print("F4X_D_ACTIVE_PAIRLIST_VALIDATION")
    print("runtime=", runtime)
    print("proposal_exists=", proposal_path.exists())
    print("pairlist_exists=", pairlist_path.exists())
    print("decision=", proposal.get("decision"))
    print("apply=", proposal.get("apply"))
    print("promotion_count=", proposal.get("promotion_count"))
    print("added_count=", proposal.get("added_count"))
    print("final_pair_count=", proposal.get("final_pair_count"))

    if not proposal_path.exists():
        failures.append("MISSING_PROPOSAL")

    if proposal.get("live") != "HOLD":
        failures.append("LIVE_NOT_HOLD")
    if proposal.get("risk_up") != "HOLD":
        failures.append("RISK_UP_NOT_HOLD")
    if proposal.get("gate_loosen") != "HOLD":
        failures.append("GATE_LOOSEN_NOT_HOLD")

    if args.expect_applied:
        pairs = pairlist.get("pairs", []) if isinstance(pairlist, dict) else []
        if not pairs:
            failures.append("PAIRLIST_EMPTY_AFTER_APPLY")
        for p in proposal.get("added_pairs", []):
            if p not in pairs:
                failures.append("ADDED_PAIR_NOT_IN_PAIRLIST:" + str(p))
        if pairlist.get("f4x_d_active_observation_enabled") is not True:
            failures.append("F4X_D_FLAG_NOT_SET")

    print("failures=", len(failures))
    for f in failures:
        print("FAIL:" + f)

    if failures:
        return 1

    print("F4X_D_ACTIVE_PAIRLIST_VALIDATION_PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
