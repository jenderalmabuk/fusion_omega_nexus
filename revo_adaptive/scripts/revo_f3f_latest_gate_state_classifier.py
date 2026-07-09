#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from datetime import datetime, timezone
from collections import Counter
from typing import Any, Dict, List, Optional


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def norm(v: Any) -> str:
    if v is None:
        return "UNKNOWN"
    s = str(v).strip()
    return s if s else "UNKNOWN"


def as_float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except Exception:
        return default


def as_int(v: Any, default: int = 0) -> int:
    try:
        return int(float(v))
    except Exception:
        return default


def parse_dt(v: Any) -> Optional[datetime]:
    if not v:
        return None
    text = str(v).strip()
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        text = text.replace(" UTC", "+00:00")
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        pass

    try:
        dt = datetime.strptime(text[:19], "%Y-%m-%d %H:%M:%S")
        return dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def event_dt(row: Dict[str, Any]) -> datetime:
    dt = parse_dt(row.get("ts")) or parse_dt(row.get("candle"))
    return dt or datetime.min.replace(tzinfo=timezone.utc)


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return default


def read_jsonl_tail(path: Path, max_lines: int) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    out = []
    for line in lines[-max_lines:]:
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out


def side_fields(side: str) -> Dict[str, str]:
    side = side.upper()
    if side == "LONG":
        return {
            "final_allow": "final_allow_long",
            "gate_allow": "gate_allow_long",
            "score_allow": "score_would_allow_long",
            "final_reason": "final_reason_long",
            "gate_reason": "gate_reason_long",
            "shadow_grade": "shadow_trade_grade_long",
            "shadow_score": "shadow_confluence_score_long",
            "mandatory": "shadow_mandatory_pass_long",
        }
    if side == "SHORT":
        return {
            "final_allow": "final_allow_short",
            "gate_allow": "gate_allow_short",
            "score_allow": "score_would_allow_short",
            "final_reason": "final_reason_short",
            "gate_reason": "gate_reason_short",
            "shadow_grade": "shadow_trade_grade_short",
            "shadow_score": "shadow_confluence_score_short",
            "mandatory": "shadow_mandatory_pass_short",
        }
    return {}


def classify_reason(reason: str) -> str:
    r = norm(reason).upper()
    if r in {"UNKNOWN", "NONE", ""}:
        return "NO_REASON"
    if "ALLOW" in r:
        return "ALLOW"
    if "TIMING" in r:
        return "TIMING_DENY"
    if "LONG_IN_PREMIUM" in r or "SHORT_IN_DISCOUNT" in r:
        return "LOCATION_DENY"
    if "RANGING_MID_RANGE" in r or "LOCATION" in r:
        return "LOCATION_DENY"
    if "TPSL" in r or "GEOMETRY" in r or "EQ" in r:
        return "GEOMETRY_DENY"
    if "FLOW_TRAP" in r or "TRAP" in r:
        return "TRAP_DENY"
    if "FLOW_DIRECTION" in r or "FLOW_NOT" in r:
        return "FLOW_DIRECTION_DENY"
    if "CONTEXT" in r or "CONTRACT" in r:
        return "CONTEXT_DENY"
    if "STICKY" in r:
        return "STICKY_DENY"
    if "CHOP" in r:
        return "CHOP_DENY"
    return "OTHER_DENY"


def desired_direction(side: str) -> str:
    if side.upper() == "LONG":
        return "LONG_ONLY"
    if side.upper() == "SHORT":
        return "SHORT_ONLY"
    return "UNKNOWN"


def opposite_direction(side: str) -> str:
    if side.upper() == "LONG":
        return "SHORT_ONLY"
    if side.upper() == "SHORT":
        return "LONG_ONLY"
    return "UNKNOWN"


def location_state(side: str, pd_zone: str, reason: str) -> str:
    side = side.upper()
    z = norm(pd_zone).upper()
    r = norm(reason).upper()

    if side == "LONG":
        if z == "DISCOUNT":
            return "LOCATION_GOOD_FOR_LONG"
        if z == "PREMIUM" or "LONG_IN_PREMIUM" in r:
            return "WAIT_LOCATION_PREMIUM_FOR_LONG"
        if z == "MID" or "RANGING_MID_RANGE" in r:
            return "WAIT_LOCATION_MID_FOR_LONG"
    if side == "SHORT":
        if z == "PREMIUM":
            return "LOCATION_GOOD_FOR_SHORT"
        if z == "DISCOUNT" or "SHORT_IN_DISCOUNT" in r:
            return "WAIT_LOCATION_DISCOUNT_FOR_SHORT"
        if z == "MID" or "RANGING_MID_RANGE" in r:
            return "WAIT_LOCATION_MID_FOR_SHORT"

    return "LOCATION_UNKNOWN"


def load_f3d_candidates(runtime: Path) -> List[Dict[str, Any]]:
    state = load_json(runtime / "revo_f3d_current_flow_snapshot_scorer_state.json", {})
    rows = state.get("flow_ready", []) if isinstance(state, dict) else []

    best: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        pair = norm(r.get("pair"))
        side = norm(r.get("side")).upper()
        if pair == "UNKNOWN" or side not in {"LONG", "SHORT"}:
            continue
        key = f"{pair}|{side}"
        old = best.get(key)
        if old is None or as_float(r.get("ratio")) > as_float(old.get("ratio")):
            best[key] = r

    return list(best.values())


def latest_events_for_pair(events: List[Dict[str, Any]], pair: str, n: int) -> List[Dict[str, Any]]:
    rows = [e for e in events if norm(e.get("pair")) == pair]
    rows.sort(key=event_dt)
    return rows[-n:]


def classify_candidate(candidate: Dict[str, Any], events: List[Dict[str, Any]], recent_n: int) -> Dict[str, Any]:
    pair = norm(candidate.get("pair"))
    side = norm(candidate.get("side")).upper()
    fields = side_fields(side)
    recent = latest_events_for_pair(events, pair, recent_n)

    if not recent:
        return {
            "pair": pair,
            "side": side,
            "latest_state": "NO_LATEST_GATE_EVENT",
            "recommended_action": "HOLD_NO_TELEMETRY",
            "event_count": 0,
            "f3d_score": candidate.get("score"),
            "f3d_max_score": candidate.get("max_score"),
            "f3d_ratio": as_float(candidate.get("ratio")),
            "details": {},
            "recent_reason_classes": [],
            "recent_final_reasons": [],
            "behavior_change": "NONE",
            "entry_gate_change": "NONE",
            "risk_change": "NONE",
        }

    latest = recent[-1]
    latest_reason = norm(latest.get(fields.get("final_reason", "")))
    latest_gate_reason = norm(latest.get(fields.get("gate_reason", "")))
    reason_class = classify_reason(latest_reason)

    final_allow = as_int(latest.get(fields.get("final_allow", "")))
    gate_allow = as_int(latest.get(fields.get("gate_allow", "")))
    score_allow = as_int(latest.get(fields.get("score_allow", "")))
    mandatory = as_int(latest.get(fields.get("mandatory", "")))

    pd_zone = norm(latest.get("pd_zone")).upper()
    regime = norm(latest.get("regime_router")).upper()
    direction_engine = norm(latest.get("direction_engine")).upper()
    flow_direction = norm(latest.get("flow_direction")).upper()
    score_vs_gate = norm(latest.get("score_vs_gate"))
    shadow_grade = norm(latest.get(fields.get("shadow_grade", "")))
    shadow_score = as_float(latest.get(fields.get("shadow_score", "")))

    want_dir = desired_direction(side)
    opp_dir = opposite_direction(side)

    direction_aligned = direction_engine == want_dir and flow_direction == want_dir
    direction_opposite = direction_engine == opp_dir or flow_direction == opp_dir
    trap_latest = direction_engine == "TRAP_RISK" or flow_direction == "TRAP_RISK" or reason_class == "TRAP_DENY"

    loc_state = location_state(side, pd_zone, latest_reason)

    recent_reason_classes = Counter()
    recent_final_reasons = Counter()
    recent_direction = Counter()
    recent_zone = Counter()
    recent_allow = 0

    for e in recent:
        fr = norm(e.get(fields.get("final_reason", "")))
        recent_final_reasons[fr] += 1
        recent_reason_classes[classify_reason(fr)] += 1
        recent_direction[norm(e.get("direction_engine"))] += 1
        recent_zone[norm(e.get("pd_zone"))] += 1
        recent_allow += as_int(e.get(fields.get("final_allow", "")))

    if final_allow or gate_allow:
        latest_state = "ENTRY_READY_SHADOW"
        recommended_action = "OBSERVE_ENTRY_OUTCOME_ONLY"
    elif direction_opposite:
        latest_state = "INVALIDATED_DIRECTION"
        recommended_action = "EXPIRE_FLOW_READY_CANDIDATE"
    elif trap_latest:
        latest_state = "AVOID_TRAP"
        recommended_action = "DO_NOT_LOOSEN_GATE"
    elif reason_class == "CONTEXT_DENY":
        latest_state = "CONTEXT_BLOCK"
        recommended_action = "FIX_CONTEXT_OR_WAIT_NEXT_CYCLE"
    elif reason_class == "GEOMETRY_DENY":
        latest_state = "GEOMETRY_BLOCK"
        recommended_action = "DO_NOT_LOOSEN_GEOMETRY"
    elif reason_class == "LOCATION_DENY":
        latest_state = "WAIT_LOCATION"
        recommended_action = "KEEP_WATCH_UNTIL_LOCATION_VALID"
    elif reason_class == "TIMING_DENY":
        latest_state = "WAIT_TRIGGER"
        recommended_action = "KEEP_WATCH_UNTIL_TRIGGER"
    elif reason_class == "FLOW_DIRECTION_DENY" and not direction_aligned:
        latest_state = "FLOW_DIRECTION_BLOCK"
        recommended_action = "WAIT_OR_EXPIRE_IF_PERSISTENT"
    elif score_allow and not gate_allow:
        latest_state = "SCORE_READY_GATE_DENY"
        recommended_action = "AUDIT_PRECISE_GATE_BLOCK"
    else:
        latest_state = "HOLD_GATE_DENY_OTHER"
        recommended_action = "HOLD_NO_PATCH"

    return {
        "pair": pair,
        "side": side,
        "latest_state": latest_state,
        "recommended_action": recommended_action,
        "event_count": len(recent),
        "f3d_score": candidate.get("score"),
        "f3d_max_score": candidate.get("max_score"),
        "f3d_ratio": as_float(candidate.get("ratio")),
        "latest_ts": norm(latest.get("ts")),
        "latest_candle": norm(latest.get("candle")),
        "latest_reason": latest_reason,
        "latest_gate_reason": latest_gate_reason,
        "reason_class": reason_class,
        "final_allow": final_allow,
        "gate_allow": gate_allow,
        "score_allow": score_allow,
        "mandatory": mandatory,
        "score_vs_gate": score_vs_gate,
        "regime": regime,
        "pd_zone": pd_zone,
        "location_state": loc_state,
        "direction_engine": direction_engine,
        "flow_direction": flow_direction,
        "desired_direction": want_dir,
        "direction_aligned": int(direction_aligned),
        "direction_opposite": int(direction_opposite),
        "shadow_grade": shadow_grade,
        "shadow_score": shadow_score,
        "recent_final_allow_sum": recent_allow,
        "recent_reason_classes": recent_reason_classes.most_common(),
        "recent_final_reasons": recent_final_reasons.most_common(),
        "recent_directions": recent_direction.most_common(),
        "recent_zones": recent_zone.most_common(),
        "latest_event_raw": {
            "event": latest.get("event"),
            "ts": latest.get("ts"),
            "candle": latest.get("candle"),
            "score_vs_gate": latest.get("score_vs_gate"),
            "final_reason": latest_reason,
            "gate_reason": latest_gate_reason,
            "regime_router": latest.get("regime_router"),
            "pd_zone": latest.get("pd_zone"),
            "direction_engine": latest.get("direction_engine"),
            "flow_direction": latest.get("flow_direction"),
            "shadow_grade": shadow_grade,
            "shadow_score": shadow_score,
        },
        "behavior_change": "NONE",
        "entry_gate_change": "NONE",
        "risk_change": "NONE",
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runtime-dir", default="user_data/revo_alpha/runtime/bybit")
    ap.add_argument("--jsonl-tail-lines", type=int, default=30000)
    ap.add_argument("--recent-n", type=int, default=12)
    args = ap.parse_args()

    runtime = Path(args.runtime_dir)

    candidates = load_f3d_candidates(runtime)

    heartbeat = read_jsonl_tail(runtime / "revo_gate_heartbeat_events.jsonl", args.jsonl_tail_lines)
    shadow = read_jsonl_tail(runtime / "revo_gate_shadow_events.jsonl", args.jsonl_tail_lines)

    for e in heartbeat:
        e["_source_file"] = "heartbeat"
    for e in shadow:
        e["_source_file"] = "shadow"

    events = heartbeat + shadow
    events.sort(key=event_dt)

    reports = [classify_candidate(c, events, args.recent_n) for c in candidates]

    state_counts = Counter(r["latest_state"] for r in reports)
    action_counts = Counter(r["recommended_action"] for r in reports)
    reason_counts = Counter(r.get("reason_class", "UNKNOWN") for r in reports)
    latest_pairs = [f"{r['pair']}|{r['side']}={r['latest_state']}" for r in reports]

    entry_ready = [r for r in reports if r["latest_state"] == "ENTRY_READY_SHADOW"]
    wait_location = [r for r in reports if r["latest_state"] == "WAIT_LOCATION"]
    wait_trigger = [r for r in reports if r["latest_state"] == "WAIT_TRIGGER"]
    invalidated = [r for r in reports if r["latest_state"] == "INVALIDATED_DIRECTION"]
    avoid_trap = [r for r in reports if r["latest_state"] == "AVOID_TRAP"]
    hard_blocks = [
        r for r in reports
        if r["latest_state"] in {"CONTEXT_BLOCK", "GEOMETRY_BLOCK", "FLOW_DIRECTION_BLOCK", "HOLD_GATE_DENY_OTHER", "SCORE_READY_GATE_DENY"}
    ]

    if entry_ready:
        decision = "F3F_ENTRY_READY_SHADOW_EXISTS_OBSERVE_ONLY"
    elif wait_location or wait_trigger:
        decision = "F3F_WATCH_MODEL_VALID_WAIT_LOCATION_OR_TRIGGER"
    elif invalidated and len(invalidated) == len(reports):
        decision = "F3F_ALL_FLOW_READY_INVALIDATED_BY_LATEST_GATE"
    elif reports:
        decision = "F3F_NO_ENTRY_READY_HOLD_BEHAVIOR_PATCH"
    else:
        decision = "F3F_NO_F3D_READY_CANDIDATES"

    payload = {
        "event": "F3F_LATEST_GATE_STATE_CLASSIFIER",
        "generated_at": utc_now(),
        "runtime_dir": str(runtime),
        "jsonl_tail_lines": args.jsonl_tail_lines,
        "recent_n": args.recent_n,
        "heartbeat_events_loaded": len(heartbeat),
        "shadow_events_loaded": len(shadow),
        "candidate_count": len(candidates),
        "latest_pairs": latest_pairs,
        "state_counts": state_counts.most_common(),
        "action_counts": action_counts.most_common(),
        "reason_counts": reason_counts.most_common(),
        "entry_ready": entry_ready,
        "wait_location": wait_location,
        "wait_trigger": wait_trigger,
        "invalidated": invalidated,
        "avoid_trap": avoid_trap,
        "hard_blocks": hard_blocks,
        "reports": reports,
        "decision": decision,
        "behavior_change": "NONE",
        "entry_gate_change": "NONE",
        "risk_change": "NONE",
    }

    out_state = runtime / "revo_f3f_latest_gate_state_classifier_state.json"
    out_compact_runtime = runtime / "F3F_LATEST_GATE_STATE_CLASSIFIER_COMPACT.txt"
    out_compact_root = Path("F3F_LATEST_GATE_STATE_CLASSIFIER_COMPACT.txt")

    out_state.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    lines = []
    lines.append("F3F_LATEST_GATE_STATE_CLASSIFIER_COMPACT")
    lines.append(f"generated_at={payload['generated_at']}")
    lines.append(f"runtime_dir={runtime}")
    lines.append("source=F3D_FLOW_READY_VS_LATEST_REAL_GATE_STATE")
    lines.append("behavior_change=NONE")
    lines.append("entry_gate_change=NONE")
    lines.append("risk_change=NONE")
    lines.append("")
    lines.append("COUNTS")
    lines.append(f"heartbeat_events_loaded={len(heartbeat)}")
    lines.append(f"shadow_events_loaded={len(shadow)}")
    lines.append(f"candidate_count={len(candidates)}")
    lines.append(f"entry_ready_count={len(entry_ready)}")
    lines.append(f"wait_location_count={len(wait_location)}")
    lines.append(f"wait_trigger_count={len(wait_trigger)}")
    lines.append(f"invalidated_count={len(invalidated)}")
    lines.append(f"avoid_trap_count={len(avoid_trap)}")
    lines.append(f"hard_block_count={len(hard_blocks)}")
    lines.append("")
    lines.append("LATEST_STATE_COUNTS")
    for k, v in state_counts.most_common():
        lines.append(f"{k}={v}")
    lines.append("")
    lines.append("RECOMMENDED_ACTION_COUNTS")
    for k, v in action_counts.most_common():
        lines.append(f"{k}={v}")
    lines.append("")
    lines.append("REASON_CLASS_COUNTS")
    for k, v in reason_counts.most_common():
        lines.append(f"{k}={v}")
    lines.append("")
    lines.append("PAIR_LATEST_STATE_DETAIL")
    for r in reports:
        lines.append(
            f"{r['pair']}|side={r['side']}|state={r['latest_state']}|action={r['recommended_action']}|"
            f"f3d={r['f3d_score']}/{r['f3d_max_score']}|ratio={r['f3d_ratio']}|"
            f"latest_ts={r.get('latest_ts')}|candle={r.get('latest_candle')}|"
            f"reason={r.get('latest_reason')}|class={r.get('reason_class')}|"
            f"regime={r.get('regime')}|zone={r.get('pd_zone')}|location={r.get('location_state')}|"
            f"direction={r.get('direction_engine')}|flow_direction={r.get('flow_direction')}|"
            f"desired={r.get('desired_direction')}|dir_aligned={r.get('direction_aligned')}|dir_opposite={r.get('direction_opposite')}|"
            f"final_allow={r.get('final_allow')}|gate_allow={r.get('gate_allow')}|score_allow={r.get('score_allow')}|"
            f"shadow_grade={r.get('shadow_grade')}|shadow_score={r.get('shadow_score')}"
        )
        lines.append(f"  recent_reason_classes={r.get('recent_reason_classes')}")
        lines.append(f"  recent_final_reasons={r.get('recent_final_reasons')}")
        lines.append(f"  recent_directions={r.get('recent_directions')}")
        lines.append(f"  recent_zones={r.get('recent_zones')}")
    lines.append("")
    lines.append("ENTRY_READY_SHADOW_AUDIT")
    for r in entry_ready:
        lines.append(f"{r['pair']}|side={r['side']}|latest_ts={r.get('latest_ts')}|reason={r.get('latest_reason')}")
    lines.append("")
    lines.append("WAIT_LOCATION_AUDIT")
    for r in wait_location:
        lines.append(
            f"{r['pair']}|side={r['side']}|zone={r.get('pd_zone')}|location={r.get('location_state')}|"
            f"direction={r.get('direction_engine')}|flow_direction={r.get('flow_direction')}|reason={r.get('latest_reason')}"
        )
    lines.append("")
    lines.append("WAIT_TRIGGER_AUDIT")
    for r in wait_trigger:
        lines.append(
            f"{r['pair']}|side={r['side']}|zone={r.get('pd_zone')}|direction={r.get('direction_engine')}|"
            f"flow_direction={r.get('flow_direction')}|reason={r.get('latest_reason')}"
        )
    lines.append("")
    lines.append("INVALIDATED_DIRECTION_AUDIT")
    for r in invalidated:
        lines.append(
            f"{r['pair']}|side={r['side']}|desired={r.get('desired_direction')}|"
            f"direction={r.get('direction_engine')}|flow_direction={r.get('flow_direction')}|reason={r.get('latest_reason')}"
        )
    lines.append("")
    lines.append("AVOID_TRAP_AUDIT")
    for r in avoid_trap:
        lines.append(
            f"{r['pair']}|side={r['side']}|direction={r.get('direction_engine')}|flow_direction={r.get('flow_direction')}|reason={r.get('latest_reason')}"
        )
    lines.append("")
    lines.append("DECISION")
    lines.append(decision)
    lines.append("NO_ENTRY_PROMOTION_FROM_THIS_REPORT_ALONE")
    lines.append("NO_GATE_LOOSEN")
    lines.append("NO_RISK_INCREASE")
    lines.append("F3F_OUTPUT_CAN_BE_USED_AS_WATCH_STATE_AUDIT_MODEL_ONLY")
    lines.append("NEXT_STEP_IF_WAIT_LOCATION_EXISTS: BUILD WATCH_EXPIRY_AND_RECHECK_AUDIT")
    lines.append("NEXT_STEP_IF_ENTRY_READY_EXISTS: OBSERVE_PAPER_OUTCOME_ONLY")
    lines.append("")
    lines.append("OUTPUT_FILES")
    lines.append(f"state={out_state}")
    lines.append(f"compact_runtime={out_compact_runtime}")
    lines.append(f"compact_root={out_compact_root}")

    text = "\n".join(lines) + "\n"
    out_compact_runtime.write_text(text, encoding="utf-8")
    out_compact_root.write_text(text, encoding="utf-8")
    print(text)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
