#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from datetime import datetime, timezone
from typing import Any, Dict, List, Tuple


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def norm(v: Any) -> str:
    if v is None:
        return "UNKNOWN"
    s = str(v).strip()
    return s if s else "UNKNOWN"


def as_int(v: Any, default: int = 0) -> int:
    try:
        return int(float(v))
    except Exception:
        return default


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception as e:
        return {"_load_error": repr(e), "_path": str(path)}


def write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def unique_keep_order(items: List[str]) -> List[str]:
    seen = set()
    out = []
    for x in items:
        if not x or x in seen:
            continue
        seen.add(x)
        out.append(x)
    return out


def load_current_pairs(pairlist_path: Path) -> Tuple[Dict[str, Any], List[str]]:
    data = load_json(pairlist_path, {})
    if isinstance(data, dict):
        pairs = data.get("pairs") or data.get("whitelist") or []
        if isinstance(pairs, list):
            return data, [str(x) for x in pairs]
    return {}, []


def hard_reason(row: Dict[str, Any]) -> bool:
    reasons = row.get("real_hard_reasons", [])
    if not isinstance(reasons, list):
        reasons = []
    blockers = row.get("blockers", [])
    if not isinstance(blockers, list):
        blockers = []
    text = " ".join(str(x).upper() for x in reasons + blockers)

    hard_terms = [
        "CVDOI_CONTRA_SIDE",
        "BULL_TRAP_RISK",
        "BEAR_TRAP_RISK",
        "F3G_B_EXPIRED",
        "FRESHNESS_STALE_RECHECK",
        "INVALIDATED_DIRECTION",
        "LATEST_DIRECTION_OPPOSITE",
        "AVOID_TRAP",
        "CONTEXT_BLOCK",
        "GEOMETRY_BLOCK",
        "TRUE_CVD_MISSING",
        "TRIGGER_REJECTED",
        "TRIGGER_DATA_MISSING",
    ]
    return any(t in text for t in hard_terms)


def priority_for_lane(row: Dict[str, Any]) -> int:
    lane = norm(row.get("lane"))
    score = as_int(row.get("score"))
    persistence = as_int(row.get("persistence_score"))
    watch_count = as_int(row.get("watch_count"))

    base = {
        "ENTRY_READY": 1000,
        "EXECUTION_WATCH": 850,
        "DISCOVERY_WATCH": 650,
        "RECHECK_DATA": 250,
    }.get(lane, 0)

    return base + score + persistence + (watch_count * 5)


def eligible_for_promotion(row: Dict[str, Any], min_score: int, allow_persistent_recheck: bool) -> bool:
    lane = norm(row.get("lane"))
    score = as_int(row.get("score"))
    cvdoi = norm(row.get("cvdoi_label")).upper()
    trigger = norm(row.get("trigger_status")).upper()
    smc = norm(row.get("smc_clean_label")).upper()
    fresh = norm(row.get("freshness_state")).upper()

    if hard_reason(row):
        return False

    if "HARD_REJECT" in smc or "GEOMETRY" in smc:
        return False

    if "TRAP" in cvdoi:
        return False

    if lane in {"ENTRY_READY", "EXECUTION_WATCH"}:
        return True

    if lane == "DISCOVERY_WATCH":
        if score < min_score:
            return False
        if trigger not in {"TRIGGER_CONFIRMED", "TRIGGER_WEAK"}:
            return False
        return True

    if lane == "RECHECK_DATA" and allow_persistent_recheck:
        if as_int(row.get("watch_count")) >= 2 and score >= min_score:
            return True

    return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runtime-dir", default="user_data/revo_alpha/runtime/bybit")
    ap.add_argument("--max-active-pairs", type=int, default=30)
    ap.add_argument("--max-promotions", type=int, default=12)
    ap.add_argument("--min-discovery-score", type=int, default=55)
    ap.add_argument("--allow-persistent-recheck", action="store_true")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    runtime = Path(args.runtime_dir)
    runtime.mkdir(parents=True, exist_ok=True)

    lane_path = runtime / "F4X_C_LANE_SEPARATION_FULL.json"
    pairlist_path = runtime / "pair_universe_remote.json"

    lane_state = load_json(lane_path, {})
    pairlist_state, current_pairs = load_current_pairs(pairlist_path)

    lanes = lane_state.get("lanes", []) if isinstance(lane_state, dict) else []
    if not isinstance(lanes, list):
        lanes = []

    hard_deny_pairs = set()
    promotion_rows = []

    for row in lanes:
        pair = norm(row.get("pair"))
        if pair == "UNKNOWN":
            continue

        if hard_reason(row):
            hard_deny_pairs.add(pair)
            continue

        if eligible_for_promotion(row, args.min_discovery_score, args.allow_persistent_recheck):
            priority = priority_for_lane(row)
            item = dict(row)
            item["promotion_priority"] = priority
            promotion_rows.append(item)

    promotion_rows.sort(key=lambda x: x.get("promotion_priority", 0), reverse=True)

    promoted_pairs = unique_keep_order([norm(x.get("pair")) for x in promotion_rows])[: args.max_promotions]

    filtered_current = [p for p in current_pairs if p not in hard_deny_pairs]

    final_pairs = unique_keep_order(promoted_pairs + filtered_current)[: args.max_active_pairs]

    removed_pairs = [p for p in current_pairs if p not in final_pairs]
    kept_pairs = [p for p in current_pairs if p in final_pairs]
    added_pairs = [p for p in final_pairs if p not in current_pairs]

    decision = "F4X_D_NO_PROMOTION"
    if promoted_pairs:
        decision = "F4X_D_PROMOTION_PROPOSED"
    if args.apply and promoted_pairs:
        decision = "F4X_D_PROMOTION_APPLIED"

    proposal = {
        "event": "F4X_D_DISCOVERY_WATCH_TO_ACTIVE_PAIRLIST_PROMOTION",
        "generated_at": now_utc(),
        "runtime_dir": str(runtime),
        "source_lane_state": str(lane_path),
        "source_pairlist": str(pairlist_path),
        "apply": bool(args.apply),
        "decision": decision,
        "max_active_pairs": args.max_active_pairs,
        "max_promotions": args.max_promotions,
        "min_discovery_score": args.min_discovery_score,
        "current_pair_count": len(current_pairs),
        "final_pair_count": len(final_pairs),
        "promotion_count": len(promoted_pairs),
        "added_count": len(added_pairs),
        "removed_count": len(removed_pairs),
        "kept_count": len(kept_pairs),
        "hard_deny_pair_count": len(hard_deny_pairs),
        "promoted_pairs": promoted_pairs,
        "added_pairs": added_pairs,
        "removed_pairs": removed_pairs,
        "kept_pairs": kept_pairs,
        "hard_deny_pairs": sorted(hard_deny_pairs),
        "final_pairs": final_pairs,
        "promotion_rows": promotion_rows[: args.max_promotions],
        "paper_strategy_bridge": "HOLD",
        "live": "HOLD",
        "risk_up": "HOLD",
        "gate_loosen": "HOLD",
    }

    out_json = runtime / "F4X_D_ACTIVE_OBSERVATION_PAIRLIST_PROPOSAL.json"
    out_compact = runtime / "F4X_D_ACTIVE_OBSERVATION_PAIRLIST_COMPACT.txt"
    root_compact = Path("F4X_D_ACTIVE_OBSERVATION_PAIRLIST_COMPACT.txt")

    write_json(out_json, proposal)

    if args.apply:
        new_state = dict(pairlist_state) if isinstance(pairlist_state, dict) else {}
        new_state["pairs"] = final_pairs
        new_state["pair_count"] = len(final_pairs)
        new_state["f4x_d_active_observation_enabled"] = True
        new_state["f4x_d_generated_at"] = proposal["generated_at"]
        new_state["f4x_d_promoted_pairs"] = promoted_pairs
        new_state["f4x_d_added_pairs"] = added_pairs
        new_state["f4x_d_removed_pairs"] = removed_pairs
        new_state["f4x_d_source"] = "F4X_C_LANE_SEPARATION"
        write_json(pairlist_path, new_state)

    lines = []
    lines.append("F4X_D_ACTIVE_OBSERVATION_PAIRLIST_COMPACT")
    lines.append(f"generated_at={proposal['generated_at']}")
    lines.append(f"runtime_dir={runtime}")
    lines.append("mode=DISCOVERY_WATCH_TO_ACTIVE_OBSERVATION_PAIRLIST")
    lines.append(f"apply={args.apply}")
    lines.append("paper_strategy_bridge=HOLD")
    lines.append("live=HOLD")
    lines.append("risk_up=HOLD")
    lines.append("gate_loosen=HOLD")
    lines.append("")
    lines.append("DECISION")
    lines.append(f"decision={decision}")
    lines.append("")
    lines.append("COUNTS")
    lines.append(f"current_pair_count={len(current_pairs)}")
    lines.append(f"final_pair_count={len(final_pairs)}")
    lines.append(f"promotion_count={len(promoted_pairs)}")
    lines.append(f"added_count={len(added_pairs)}")
    lines.append(f"removed_count={len(removed_pairs)}")
    lines.append(f"kept_count={len(kept_pairs)}")
    lines.append(f"hard_deny_pair_count={len(hard_deny_pairs)}")
    lines.append("")
    lines.append("PROMOTED_PAIRS")
    for row in promotion_rows[: args.max_promotions]:
        lines.append(
            f"{row.get('pair')}|side={row.get('side')}|lane={row.get('lane')}|"
            f"priority={row.get('promotion_priority')}|score={row.get('score')}|"
            f"cvdoi={row.get('cvdoi_label')}|trigger={row.get('trigger_status')}|"
            f"smc={row.get('smc_clean_label')}|fresh={row.get('freshness_state')}|"
            f"watch_count={row.get('watch_count')}|persistence={row.get('persistence_score')}"
        )
    lines.append("")
    lines.append("ADDED_PAIRS")
    for p in added_pairs:
        lines.append(p)
    lines.append("")
    lines.append("REMOVED_PAIRS")
    for p in removed_pairs:
        lines.append(p)
    lines.append("")
    lines.append("FINAL_PAIRLIST")
    for p in final_pairs:
        lines.append(p)
    lines.append("")
    lines.append("DECISION_POLICY")
    lines.append("This promotes discovery/watch candidates to active observation only.")
    lines.append("It does not enable paper entry bridge.")
    lines.append("It does not loosen gate.")
    lines.append("It does not change live trading.")
    lines.append("Next cycle must produce fresh gate/lifecycle telemetry before entry can be considered.")
    lines.append("")
    lines.append("OUTPUT_FILES")
    lines.append(f"proposal_json={out_json}")
    lines.append(f"compact={out_compact}")
    lines.append(f"pairlist={pairlist_path}")

    text = "\n".join(lines) + "\n"
    out_compact.write_text(text, encoding="utf-8")
    root_compact.write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
