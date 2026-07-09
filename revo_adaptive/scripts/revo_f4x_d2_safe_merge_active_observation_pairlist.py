#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sqlite3
from pathlib import Path
from datetime import datetime, timezone
from typing import Any, Dict, List, Tuple


PAIR_RE = re.compile(r"[A-Z0-9]+/USDT:USDT")


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def norm(v: Any) -> str:
    if v is None:
        return ""
    return str(v).strip()


def as_int(v: Any, default: int = 0) -> int:
    try:
        return int(float(v))
    except Exception:
        return default


def as_float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
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
        x = norm(x)
        if not x or x in seen:
            continue
        if not PAIR_RE.fullmatch(x):
            continue
        seen.add(x)
        out.append(x)
    return out


def load_current_pairs(pairlist_path: Path) -> Tuple[Dict[str, Any], List[str]]:
    data = load_json(pairlist_path, {})
    if isinstance(data, dict):
        pairs = data.get("pairs") or data.get("whitelist") or []
        if isinstance(pairs, list):
            return data, unique_keep_order([str(x) for x in pairs])
    return {}, []


def hard_deny_pairs_from_proposal(proposal: Dict[str, Any]) -> set:
    pairs = proposal.get("hard_deny_pairs", [])
    if not isinstance(pairs, list):
        return set()
    return set(unique_keep_order([str(x) for x in pairs]))


def promoted_pairs_from_proposal(proposal: Dict[str, Any]) -> List[str]:
    pairs = proposal.get("promoted_pairs", [])
    if isinstance(pairs, list) and pairs:
        return unique_keep_order([str(x) for x in pairs])

    rows = proposal.get("promotion_rows", [])
    if isinstance(rows, list):
        return unique_keep_order([norm(x.get("pair")) for x in rows if isinstance(x, dict)])

    return []


def rows_from_f4xc(lane_state: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows = lane_state.get("lanes", [])
    return rows if isinstance(rows, list) else []


def row_hard(row: Dict[str, Any]) -> bool:
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


def supplemental_from_lanes(lane_state: Dict[str, Any], hard_deny: set) -> List[str]:
    rows = rows_from_f4xc(lane_state)
    scored = []
    for row in rows:
        pair = norm(row.get("pair"))
        if not pair or pair in hard_deny or not PAIR_RE.fullmatch(pair):
            continue
        if row_hard(row):
            continue

        lane = norm(row.get("lane"))
        score = as_int(row.get("score"))
        watch = as_int(row.get("watch_count"))
        recheck = as_int(row.get("recheck_count"))
        persistence = as_int(row.get("persistence_score"))

        base = {
            "ENTRY_READY": 1000,
            "EXECUTION_WATCH": 850,
            "DISCOVERY_WATCH": 700,
            "RECHECK_DATA": 350,
            "DENY_SOFT": 100,
        }.get(lane, 0)

        priority = base + score + persistence + (watch * 5) + recheck
        scored.append((priority, pair))

    scored.sort(reverse=True)
    return unique_keep_order([p for _, p in scored])


def supplemental_from_f4x_signals(signals_state: Dict[str, Any], hard_deny: set) -> List[str]:
    rows = signals_state.get("signals", [])
    if not isinstance(rows, list):
        return []

    scored = []
    for row in rows:
        pair = norm(row.get("pair"))
        if not pair or pair in hard_deny or not PAIR_RE.fullmatch(pair):
            continue
        blockers = row.get("hard_blockers", [])
        if isinstance(blockers, list) and blockers:
            continue

        action = norm(row.get("paper_action"))
        score = as_int(row.get("score"))
        cvdoi = norm((row.get("cvdoi") or {}).get("cvdoi_label") if isinstance(row.get("cvdoi"), dict) else "")
        trigger = norm((row.get("trigger") or {}).get("trigger_status") if isinstance(row.get("trigger"), dict) else "")

        base = {
            "ALLOW_PAPER_ENTRY": 1000,
            "WATCH_ONLY": 650,
            "RECHECK": 250,
            "DENY": 0,
        }.get(action, 0)

        if "BULLISH_CONTINUATION" in cvdoi or "BEARISH_CONTINUATION" in cvdoi:
            base += 50
        if trigger == "TRIGGER_CONFIRMED":
            base += 40
        elif trigger == "TRIGGER_WEAK":
            base += 15

        scored.append((base + score, pair))

    scored.sort(reverse=True)
    return unique_keep_order([p for _, p in scored])


def extract_pairs_recursive(obj: Any) -> List[str]:
    out: List[str] = []

    if isinstance(obj, str):
        out.extend(PAIR_RE.findall(obj))
    elif isinstance(obj, list):
        for x in obj:
            out.extend(extract_pairs_recursive(x))
    elif isinstance(obj, dict):
        for key in ["pair", "symbol", "pair_name"]:
            v = obj.get(key)
            if isinstance(v, str):
                out.extend(PAIR_RE.findall(v))
        for v in obj.values():
            out.extend(extract_pairs_recursive(v))

    return out


def supplemental_from_json_file(path: Path, hard_deny: set) -> List[str]:
    data = load_json(path, {})
    pairs = unique_keep_order(extract_pairs_recursive(data))
    return [p for p in pairs if p not in hard_deny]


def supplemental_from_latest_flow_sqlite(runtime: Path, hard_deny: set) -> List[str]:
    db = runtime / "f3a_market_wide_flow_cache.sqlite"
    if not db.exists():
        return []

    try:
        con = sqlite3.connect(str(db))
        con.row_factory = sqlite3.Row
        rows = con.execute("select pair, turnover24h from latest_flow").fetchall()
        con.close()
    except Exception:
        return []

    scored = []
    for r in rows:
        pair = str(r["pair"])
        if pair in hard_deny or not PAIR_RE.fullmatch(pair):
            continue
        scored.append((as_float(r["turnover24h"], 0.0), pair))

    scored.sort(reverse=True)
    return unique_keep_order([p for _, p in scored])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runtime-dir", default="user_data/revo_alpha/runtime/bybit")
    ap.add_argument("--target-active-pairs", type=int, default=30)
    ap.add_argument("--min-active-pairs", type=int, default=24)
    ap.add_argument("--max-promotions", type=int, default=12)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--allow-below-min", action="store_true")
    args = ap.parse_args()

    runtime = Path(args.runtime_dir)
    runtime.mkdir(parents=True, exist_ok=True)

    pairlist_path = runtime / "pair_universe_remote.json"
    proposal_path = runtime / "F4X_D_ACTIVE_OBSERVATION_PAIRLIST_PROPOSAL.json"
    lane_path = runtime / "F4X_C_LANE_SEPARATION_FULL.json"
    signal_path = runtime / "F4X_PAPER_DECISION_SIGNALS.json"

    pairlist_state, current_pairs = load_current_pairs(pairlist_path)
    proposal = load_json(proposal_path, {})
    lane_state = load_json(lane_path, {})
    signals_state = load_json(signal_path, {})

    hard_deny = hard_deny_pairs_from_proposal(proposal)
    promoted = promoted_pairs_from_proposal(proposal)[: args.max_promotions]

    source_promoted = [p for p in promoted if p not in hard_deny]
    source_current = [p for p in current_pairs if p not in hard_deny]
    source_lanes = supplemental_from_lanes(lane_state if isinstance(lane_state, dict) else {}, hard_deny)
    source_signals = supplemental_from_f4x_signals(signals_state if isinstance(signals_state, dict) else {}, hard_deny)
    source_execution = supplemental_from_json_file(runtime / "revo_execution_context.json", hard_deny)
    source_flow = supplemental_from_json_file(runtime / "revo_flow_context.json", hard_deny)
    source_canonical = supplemental_from_json_file(runtime / "revo_flow_context_canonical.json", hard_deny)
    source_sqlite = supplemental_from_latest_flow_sqlite(runtime, hard_deny)

    merged = unique_keep_order(
        source_promoted
        + source_current
        + source_lanes
        + source_signals
        + source_execution
        + source_flow
        + source_canonical
        + source_sqlite
    )

    final_pairs = merged[: args.target_active_pairs]

    added_pairs = [p for p in final_pairs if p not in current_pairs]
    kept_pairs = [p for p in current_pairs if p in final_pairs]
    removed_pairs = [p for p in current_pairs if p not in final_pairs]
    promoted_in_final = [p for p in source_promoted if p in final_pairs]

    safe_to_apply = len(final_pairs) >= args.min_active_pairs or args.allow_below_min
    apply_performed = bool(args.apply and safe_to_apply)

    if not source_promoted:
        decision = "F4X_D2_NO_PROMOTED_PAIRS"
    elif not safe_to_apply:
        decision = "F4X_D2_HOLD_FINAL_PAIR_COUNT_BELOW_MIN"
    elif apply_performed:
        decision = "F4X_D2_SAFE_MERGE_APPLIED"
    else:
        decision = "F4X_D2_SAFE_MERGE_PROPOSED"

    payload = {
        "event": "F4X_D2_SAFE_MERGE_ACTIVE_OBSERVATION_PAIRLIST",
        "generated_at": now_utc(),
        "runtime_dir": str(runtime),
        "apply_requested": bool(args.apply),
        "apply_performed": apply_performed,
        "safe_to_apply": safe_to_apply,
        "decision": decision,
        "target_active_pairs": args.target_active_pairs,
        "min_active_pairs": args.min_active_pairs,
        "max_promotions": args.max_promotions,
        "current_pair_count": len(current_pairs),
        "final_pair_count": len(final_pairs),
        "promoted_source_count": len(source_promoted),
        "promoted_in_final_count": len(promoted_in_final),
        "added_count": len(added_pairs),
        "kept_count": len(kept_pairs),
        "removed_count": len(removed_pairs),
        "hard_deny_pair_count": len(hard_deny),
        "source_counts": {
            "promoted": len(source_promoted),
            "current": len(source_current),
            "lanes": len(source_lanes),
            "signals": len(source_signals),
            "execution": len(source_execution),
            "flow": len(source_flow),
            "canonical": len(source_canonical),
            "sqlite": len(source_sqlite),
        },
        "promoted_pairs": source_promoted,
        "promoted_in_final": promoted_in_final,
        "added_pairs": added_pairs,
        "kept_pairs": kept_pairs,
        "removed_pairs": removed_pairs,
        "hard_deny_pairs": sorted(hard_deny),
        "final_pairs": final_pairs,
        "paper_strategy_bridge": "HOLD",
        "live": "HOLD",
        "risk_up": "HOLD",
        "gate_loosen": "HOLD",
    }

    out_json = runtime / "F4X_D2_SAFE_MERGE_ACTIVE_PAIRLIST_PROPOSAL.json"
    out_compact = runtime / "F4X_D2_SAFE_MERGE_ACTIVE_PAIRLIST_COMPACT.txt"
    root_compact = Path("F4X_D2_SAFE_MERGE_ACTIVE_PAIRLIST_COMPACT.txt")

    write_json(out_json, payload)

    if apply_performed:
        backup_path = runtime / f"pair_universe_remote.pre_f4x_d2_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.json"
        if pairlist_path.exists():
            backup_path.write_text(pairlist_path.read_text(encoding="utf-8", errors="replace"), encoding="utf-8")

        new_state = dict(pairlist_state) if isinstance(pairlist_state, dict) else {}
        new_state["pairs"] = final_pairs
        new_state["pair_count"] = len(final_pairs)
        new_state["f4x_d2_safe_merge_enabled"] = True
        new_state["f4x_d2_generated_at"] = payload["generated_at"]
        new_state["f4x_d2_promoted_pairs"] = source_promoted
        new_state["f4x_d2_promoted_in_final"] = promoted_in_final
        new_state["f4x_d2_added_pairs"] = added_pairs
        new_state["f4x_d2_removed_pairs"] = removed_pairs
        new_state["f4x_d2_backup"] = str(backup_path)
        new_state["f4x_d2_source"] = "F4X_D_PROMOTION + SAFE_SUPPLEMENTAL_MERGE"
        write_json(pairlist_path, new_state)

    lines = []
    lines.append("F4X_D2_SAFE_MERGE_ACTIVE_PAIRLIST_COMPACT")
    lines.append(f"generated_at={payload['generated_at']}")
    lines.append(f"runtime_dir={runtime}")
    lines.append("mode=SAFE_MERGE_DISCOVERY_WATCH_TO_ACTIVE_OBSERVATION")
    lines.append(f"apply_requested={args.apply}")
    lines.append(f"apply_performed={apply_performed}")
    lines.append(f"safe_to_apply={safe_to_apply}")
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
    lines.append(f"target_active_pairs={args.target_active_pairs}")
    lines.append(f"min_active_pairs={args.min_active_pairs}")
    lines.append(f"promoted_source_count={len(source_promoted)}")
    lines.append(f"promoted_in_final_count={len(promoted_in_final)}")
    lines.append(f"added_count={len(added_pairs)}")
    lines.append(f"kept_count={len(kept_pairs)}")
    lines.append(f"removed_count={len(removed_pairs)}")
    lines.append(f"hard_deny_pair_count={len(hard_deny)}")
    lines.append("")
    lines.append("SOURCE_COUNTS")
    for k, v in payload["source_counts"].items():
        lines.append(f"{k}={v}")
    lines.append("")
    lines.append("PROMOTED_IN_FINAL")
    for p in promoted_in_final:
        lines.append(p)
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
    lines.append("Safe merge keeps active observation broad enough.")
    lines.append("Never applies if final_pair_count is below min_active_pairs unless explicitly allowed.")
    lines.append("This is observation only, not paper entry.")
    lines.append("Paper bridge remains disabled until ENTRY_READY appears.")
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
