#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple


GRADE_RANK = {
    "A+": 6,
    "A": 5,
    "B+": 4,
    "B": 3,
    "C": 2,
    "D": 1,
    "NONE": 0,
    "UNKNOWN": 0,
    "NA": 0,
    "": 0,
}


WATCH_STATES = {
    "A_PLUS_MISMATCH_AUDIT",
    "SETUP_VALID_WAIT_TRIGGER",
    "SETUP_VALID_WAIT_LOCATION",
    "WATCH_SETUP_FORMING",
    "READY_TO_ENTER",
}


VALID_DENY_STATES = {
    "VALID_DENY_WRONG_DIRECTION",
    "VALID_DENY_BAD_LOCATION",
    "VALID_DENY_TRAP_RISK",
    "VALID_DENY_RANGING_MID",
    "CONTEXT_MISS_DENY",
    "LOW_QUALITY_DENY",
}


def parse_ts(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        text = str(value)
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8", errors="replace"))


def read_jsonl(path: Path, cutoff: Optional[datetime]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    if not path.exists():
        return rows

    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except Exception:
            continue

        ts = parse_ts(obj.get("ts") or obj.get("generated_at"))
        if cutoff is not None and ts is not None and ts < cutoff:
            continue

        rows.append(obj)

    return rows


def as_int(value: Any) -> int:
    try:
        return int(value or 0)
    except Exception:
        return 0


def as_float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except Exception:
        return 0.0


def norm(value: Any) -> str:
    if value is None:
        return "UNKNOWN"
    text = str(value).strip()
    return text if text else "UNKNOWN"


def grade_rank(value: Any) -> int:
    return GRADE_RANK.get(norm(value).upper(), 0)


def side_fields(event: Dict[str, Any], side: str) -> Dict[str, Any]:
    side = side.lower()
    suffix = "long" if side == "long" else "short"

    return {
        "side": side.upper(),
        "score_would_allow": as_int(event.get(f"score_would_allow_{suffix}")),
        "gate_allow": as_int(event.get(f"gate_allow_{suffix}")),
        "final_allow": as_int(event.get(f"final_allow_{suffix}")),
        "final_reason": norm(event.get(f"final_reason_{suffix}")),
        "gate_reason": norm(event.get(f"gate_reason_{suffix}")),
        "shadow_grade": norm(event.get(f"shadow_trade_grade_{suffix}")),
        "shadow_score": as_float(event.get(f"shadow_confluence_score_{suffix}")),
        "shadow_veto": norm(event.get(f"shadow_hard_veto_reason_{suffix}")),
        "family_grade": norm(event.get(f"v139_family_grade_{suffix}")),
        "family_score": as_float(event.get(f"v139_family_score_{suffix}")),
        "family_action": norm(event.get(f"v139_recommended_action_{suffix}")),
        "family_hard_veto": norm(event.get(f"v139_hard_veto_{suffix}")),
        "family_soft_veto": norm(event.get(f"v139_soft_veto_{suffix}")),
        "tape_gate": norm(event.get(f"v139_tape_gate_{suffix}")),
        "location_rule": norm(event.get(f"v139_location_rule_{suffix}")),
        "tp_room_pct": as_float(event.get(f"tp_room_{suffix}_pct")),
    }


def collect_reasons(sf: Dict[str, Any]) -> List[str]:
    keys = [
        "final_reason",
        "gate_reason",
        "shadow_veto",
        "family_hard_veto",
        "family_soft_veto",
        "tape_gate",
        "location_rule",
    ]
    out = []
    for key in keys:
        value = norm(sf.get(key))
        if value not in {"NONE", "NA", "UNKNOWN", "0", "FALSE"}:
            out.append(value)
    return out


def direction_ok(event: Dict[str, Any], side: str) -> bool:
    direction = norm(event.get("direction_engine") or event.get("flow_direction")).upper()
    flow_direction = norm(event.get("flow_direction")).upper()

    if side == "LONG":
        return direction == "LONG_ONLY" or flow_direction == "LONG_ONLY"
    return direction == "SHORT_ONLY" or flow_direction == "SHORT_ONLY"


def location_ideal(event: Dict[str, Any], side: str) -> bool:
    zone = norm(event.get("pd_zone")).upper()
    if side == "LONG":
        return zone == "DISCOUNT"
    return zone == "PREMIUM"


def location_bad(event: Dict[str, Any], side: str, reasons: List[str]) -> bool:
    zone = norm(event.get("pd_zone")).upper()
    reason_text = " ".join(reasons).upper()

    if side == "LONG":
        return zone == "PREMIUM" or "LONG_IN_PREMIUM" in reason_text
    return zone == "DISCOUNT" or "SHORT_IN_DISCOUNT" in reason_text


def is_ranging_mid(event: Dict[str, Any], reasons: List[str]) -> bool:
    zone = norm(event.get("pd_zone")).upper()
    regime = norm(event.get("regime_router")).upper()
    reason_text = " ".join(reasons).upper()
    return zone == "MID" and ("RANGING" in regime or "RANGING_MID_RANGE" in reason_text)


def trap_risk(event: Dict[str, Any], reasons: List[str]) -> bool:
    text = " ".join([
        norm(event.get("flow_risk")),
        norm(event.get("direction_engine")),
        norm(event.get("flow_direction")),
        " ".join(reasons),
    ]).upper()
    return "TRAP" in text or "DENY_FLOW_TRAP_RISK" in text


def context_miss(event: Dict[str, Any]) -> bool:
    text = " ".join([
        norm(event.get("context_contract_status")),
        norm(event.get("flow_lookup_source")),
        norm(event.get("flow_data_quality")),
        norm(event.get("scanner_mode")),
    ]).upper()
    return "CONTEXT_MISS" in text or "EXECUTION_CONTEXT_MISS_DENY" in text


def timing_blocked(reasons: List[str]) -> bool:
    text = " ".join(reasons).upper()
    return "DENY_TIMING" in text or "TIMING" in text and "ALLOW" not in text


def gate_allow_reason(sf: Dict[str, Any]) -> bool:
    text = " ".join([norm(sf.get("gate_reason")), norm(sf.get("final_reason"))]).upper()
    return "ALLOW_FLOW_TIMING_GEOMETRY" in text or text.startswith("ALLOW")


def high_quality(sf: Dict[str, Any]) -> bool:
    rank = max(grade_rank(sf.get("shadow_grade")), grade_rank(sf.get("family_grade")))
    score = max(as_float(sf.get("shadow_score")), as_float(sf.get("family_score")))
    return rank >= GRADE_RANK["B+"] or score >= 80.0


def elite_quality(sf: Dict[str, Any]) -> bool:
    rank = max(grade_rank(sf.get("shadow_grade")), grade_rank(sf.get("family_grade")))
    score = max(as_float(sf.get("shadow_score")), as_float(sf.get("family_score")))
    return rank >= GRADE_RANK["A"] or score >= 95.0


def classify_side(event: Dict[str, Any], side: str) -> Dict[str, Any]:
    side = side.upper()
    sf = side_fields(event, side.lower())
    reasons = collect_reasons(sf)

    d_ok = direction_ok(event, side)
    loc_ideal = location_ideal(event, side)
    loc_bad = location_bad(event, side, reasons)
    r_mid = is_ranging_mid(event, reasons)
    trap = trap_risk(event, reasons)
    c_miss = context_miss(event)
    t_block = timing_blocked(reasons)
    q_high = high_quality(sf)
    q_elite = elite_quality(sf)
    gate_allow = gate_allow_reason(sf)

    score_vs_gate = norm(event.get("score_vs_gate"))
    final_allow = as_int(sf.get("final_allow"))

    setup_state = "LOW_QUALITY_DENY"
    watch_reason = "LOW_QUALITY_OR_MULTI_BLOCKER"
    action = "REJECT_AUDIT_ONLY"

    if final_allow:
        setup_state = "READY_TO_ENTER"
        watch_reason = "FINAL_GATE_ALLOW"
        action = "READY_AUDIT_ONLY"

    elif c_miss:
        setup_state = "CONTEXT_MISS_DENY"
        watch_reason = "EXECUTION_CONTEXT_MISS"
        action = "FIX_CONTEXT_BEFORE_ENTRY"

    elif trap:
        setup_state = "VALID_DENY_TRAP_RISK"
        watch_reason = "FLOW_TRAP_RISK"
        action = "REJECT_AUDIT_ONLY"

    elif score_vs_gate == "SCORE_DENY_GATE_ALLOW" or "SCORE_GATE_MISMATCH" in " ".join(reasons).upper():
        if q_elite or gate_allow:
            setup_state = "A_PLUS_MISMATCH_AUDIT"
            watch_reason = "GATE_OR_SCORE_MISMATCH_ON_HIGH_QUALITY_SETUP"
            action = "WATCH_AUDIT_ONLY"
        else:
            setup_state = "LOW_QUALITY_DENY"
            watch_reason = "SCORE_GATE_MISMATCH_LOW_QUALITY"
            action = "REJECT_AUDIT_ONLY"

    elif not d_ok:
        setup_state = "VALID_DENY_WRONG_DIRECTION"
        watch_reason = "DIRECTION_NOT_ALIGNED"
        action = "REJECT_AUDIT_ONLY"

    elif loc_bad:
        if q_high:
            setup_state = "SETUP_VALID_WAIT_LOCATION"
            watch_reason = "DIRECTION_OK_BUT_WAIT_BETTER_LOCATION"
            action = "WATCH_LOCATION_AUDIT_ONLY"
        else:
            setup_state = "VALID_DENY_BAD_LOCATION"
            watch_reason = "BAD_PD_LOCATION"
            action = "REJECT_AUDIT_ONLY"

    elif r_mid:
        if q_high and d_ok:
            setup_state = "SETUP_VALID_WAIT_LOCATION"
            watch_reason = "RANGING_MID_WAIT_EDGE_LOCATION"
            action = "WATCH_LOCATION_AUDIT_ONLY"
        else:
            setup_state = "VALID_DENY_RANGING_MID"
            watch_reason = "RANGING_MID_NO_EDGE"
            action = "REJECT_AUDIT_ONLY"

    elif d_ok and loc_ideal and t_block:
        setup_state = "SETUP_VALID_WAIT_TRIGGER"
        watch_reason = "DIRECTION_AND_LOCATION_OK_WAIT_TIMING_TRIGGER"
        action = "WATCH_TRIGGER_AUDIT_ONLY"

    elif d_ok and loc_ideal and q_high:
        setup_state = "WATCH_SETUP_FORMING"
        watch_reason = "DIRECTION_LOCATION_QUALITY_OK_BUT_NOT_FINAL"
        action = "WATCH_SETUP_AUDIT_ONLY"

    elif d_ok and q_high:
        setup_state = "SETUP_VALID_WAIT_LOCATION"
        watch_reason = "DIRECTION_AND_QUALITY_OK_WAIT_LOCATION"
        action = "WATCH_LOCATION_AUDIT_ONLY"

    elif d_ok:
        setup_state = "WATCH_SETUP_FORMING"
        watch_reason = "DIRECTION_OK_BUT_CONFLUENCE_NOT_ENOUGH"
        action = "WATCH_LOW_PRIORITY_AUDIT_ONLY"

    return {
        "event": "F2U_SETUP_STATE",
        "pair": norm(event.get("pair")),
        "candle": norm(event.get("candle")),
        "ts": norm(event.get("ts") or event.get("generated_at")),
        "side": side,
        "setup_state": setup_state,
        "watch_reason": watch_reason,
        "action": action,
        "direction_ok": int(d_ok),
        "location_ideal": int(loc_ideal),
        "location_bad": int(loc_bad),
        "timing_blocked": int(t_block),
        "trap_risk": int(trap),
        "context_miss": int(c_miss),
        "high_quality": int(q_high),
        "elite_quality": int(q_elite),
        "score_vs_gate": score_vs_gate,
        "score_would_allow": as_int(sf.get("score_would_allow")),
        "gate_allow": as_int(sf.get("gate_allow")),
        "final_allow": final_allow,
        "final_reason": sf.get("final_reason"),
        "gate_reason": sf.get("gate_reason"),
        "shadow_grade": sf.get("shadow_grade"),
        "shadow_score": sf.get("shadow_score"),
        "family_grade": sf.get("family_grade"),
        "family_score": sf.get("family_score"),
        "pd_zone": norm(event.get("pd_zone")),
        "pd_location": event.get("pd_location"),
        "regime_router": norm(event.get("regime_router")),
        "direction_engine": norm(event.get("direction_engine") or event.get("flow_direction")),
        "flow_direction": norm(event.get("flow_direction")),
        "flow_risk": norm(event.get("flow_risk")),
        "flow_authority": norm(event.get("flow_authority")),
        "flow_data_quality": norm(event.get("flow_data_quality")),
        "context_contract_status": norm(event.get("context_contract_status")),
        "flow_lookup_source": norm(event.get("flow_lookup_source")),
        "blockers": reasons,
    }


def best_key(row: Dict[str, Any]) -> Tuple[int, float, int]:
    state_priority = {
        "READY_TO_ENTER": 100,
        "A_PLUS_MISMATCH_AUDIT": 90,
        "SETUP_VALID_WAIT_TRIGGER": 80,
        "SETUP_VALID_WAIT_LOCATION": 70,
        "WATCH_SETUP_FORMING": 60,
        "VALID_DENY_TRAP_RISK": 20,
        "VALID_DENY_WRONG_DIRECTION": 10,
        "VALID_DENY_BAD_LOCATION": 10,
        "VALID_DENY_RANGING_MID": 10,
        "CONTEXT_MISS_DENY": 5,
        "LOW_QUALITY_DENY": 0,
    }
    return (
        state_priority.get(row.get("setup_state"), 0),
        float(row.get("shadow_score") or 0.0),
        int(row.get("elite_quality") or 0),
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runtime-dir", default="user_data/revo_alpha/runtime/bybit")
    ap.add_argument("--cutoff", default="2026-05-09T02:08:56+00:00")
    ap.add_argument("--max-events", type=int, default=0)
    args = ap.parse_args()

    runtime = Path(args.runtime_dir)
    cutoff = parse_ts(args.cutoff)

    input_files = [
        runtime / "revo_gate_heartbeat_events.jsonl",
        runtime / "revo_gate_shadow_events.jsonl",
    ]

    raw_events: List[Dict[str, Any]] = []
    for path in input_files:
        raw_events.extend(read_jsonl(path, cutoff))

    if args.max_events and args.max_events > 0:
        raw_events = raw_events[-args.max_events:]

    classified: List[Dict[str, Any]] = []
    for event in raw_events:
        classified.append(classify_side(event, "LONG"))
        classified.append(classify_side(event, "SHORT"))

    state_counts = Counter(row["setup_state"] for row in classified)
    action_counts = Counter(row["action"] for row in classified)
    pair_state_counts = Counter((row["pair"], row["setup_state"]) for row in classified)
    side_state_counts = Counter((row["side"], row["setup_state"]) for row in classified)
    watch_rows = [r for r in classified if r["setup_state"] in WATCH_STATES]
    valid_deny_rows = [r for r in classified if r["setup_state"] in VALID_DENY_STATES]

    best_by_pair_side: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for row in classified:
        key = (row["pair"], row["side"])
        old = best_by_pair_side.get(key)
        if old is None or best_key(row) > best_key(old):
            best_by_pair_side[key] = row

    best_watch = [
        row for row in best_by_pair_side.values()
        if row["setup_state"] in WATCH_STATES
    ]
    best_watch.sort(key=best_key, reverse=True)

    out_jsonl = runtime / "revo_f2u_setup_state_events.jsonl"
    out_latest = runtime / "revo_f2u_setup_state_latest.json"
    out_compact = Path("F2U_SETUP_STATE_CLASSIFIER_COMPACT.txt")

    with out_jsonl.open("w", encoding="utf-8") as handle:
        for row in classified:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")

    latest_payload = {
        "event": "F2U_SETUP_STATE_LATEST",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "runtime_dir": str(runtime),
        "cutoff": args.cutoff,
        "raw_events": len(raw_events),
        "classified_side_events": len(classified),
        "state_counts": state_counts.most_common(),
        "action_counts": action_counts.most_common(),
        "best_watch_count": len(best_watch),
        "best_watch_top": best_watch[:50],
    }
    out_latest.write_text(json.dumps(latest_payload, indent=2, ensure_ascii=False), encoding="utf-8")

    lines: List[str] = []
    lines.append("F2U_SETUP_STATE_CLASSIFIER_COMPACT")
    lines.append(f"generated_at={datetime.now(timezone.utc).isoformat()}")
    lines.append(f"runtime_dir={runtime}")
    lines.append(f"cutoff={args.cutoff}")
    lines.append(f"raw_events={len(raw_events)}")
    lines.append(f"classified_side_events={len(classified)}")
    lines.append("")
    lines.append("STATE_COUNTS")
    for k, v in state_counts.most_common():
        lines.append(f"{k}={v}")
    lines.append("")
    lines.append("ACTION_COUNTS")
    for k, v in action_counts.most_common():
        lines.append(f"{k}={v}")
    lines.append("")
    lines.append("SIDE_STATE_COUNTS")
    for (side, state), v in side_state_counts.most_common(30):
        lines.append(f"{side}|{state}={v}")
    lines.append("")
    lines.append("WATCH_SUMMARY")
    lines.append(f"watch_rows={len(watch_rows)}")
    lines.append(f"valid_deny_rows={len(valid_deny_rows)}")
    lines.append(f"best_watch_unique_pair_side={len(best_watch)}")
    lines.append("")
    lines.append("TOP_WATCH_CANDIDATES")
    for row in best_watch[:40]:
        lines.append(
            "|".join([
                row["setup_state"],
                row["pair"],
                row["side"],
                f"grade={row['shadow_grade']}",
                f"score={row['shadow_score']}",
                f"family_grade={row['family_grade']}",
                f"family_score={row['family_score']}",
                f"zone={row['pd_zone']}",
                f"direction={row['direction_engine']}",
                f"regime={row['regime_router']}",
                f"reason={row['watch_reason']}",
                f"final={row['final_reason']}",
                f"gate={row['gate_reason']}",
            ])
        )
    lines.append("")
    lines.append("TOP_PAIR_STATE_COUNTS")
    for (pair, state), v in pair_state_counts.most_common(40):
        lines.append(f"{pair}|{state}={v}")
    lines.append("")
    lines.append("OUTPUT_FILES")
    lines.append(f"jsonl={out_jsonl}")
    lines.append(f"latest={out_latest}")
    lines.append(f"compact={out_compact}")
    lines.append("")
    lines.append("DECISION_HINT")
    lines.append("If SETUP_VALID_WAIT_TRIGGER dominates, build watching trigger engine before loosening timing.")
    lines.append("If SETUP_VALID_WAIT_LOCATION dominates, build location waiting engine.")
    lines.append("If A_PLUS_MISMATCH_AUDIT appears, audit score-gate mismatch safety on those pairs only.")
    lines.append("If VALID_DENY_WRONG_DIRECTION/TRAP/BAD_LOCATION dominates, gate is likely correct.")
    lines.append("No entry/gate/risk behavior changed by F2U.")

    out_compact.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(out_compact.read_text(encoding="utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
