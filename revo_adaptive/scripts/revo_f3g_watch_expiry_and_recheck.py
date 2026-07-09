#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from datetime import datetime, timezone
from collections import Counter
from typing import Any, Dict, List, Optional


ACTIVE_STATES = {
    "WAIT_LOCATION",
    "WAIT_TRIGGER",
    "ENTRY_READY_SHADOW",
    "SCORE_READY_GATE_DENY",
    "FLOW_DIRECTION_BLOCK",
    "HOLD_GATE_DENY_OTHER",
}

EXPIRE_STATES = {
    "INVALIDATED_DIRECTION",
    "AVOID_TRAP",
    "CONTEXT_BLOCK",
    "GEOMETRY_BLOCK",
    "NO_LATEST_GATE_EVENT",
}


def utc_now_dt() -> datetime:
    return datetime.now(timezone.utc)


def utc_now() -> str:
    return utc_now_dt().isoformat()


def norm(v: Any) -> str:
    if v is None:
        return "UNKNOWN"
    s = str(v).strip()
    return s if s else "UNKNOWN"


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
        return None


def minutes_between(a: Optional[datetime], b: Optional[datetime]) -> float:
    if a is None or b is None:
        return 0.0
    return round((b - a).total_seconds() / 60.0, 3)


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
    except Exception:
        return default


def key_for(pair: str, side: str) -> str:
    return f"{pair}|{side}"


def load_f3f_reports(runtime: Path) -> List[Dict[str, Any]]:
    state = load_json(runtime / "revo_f3f_latest_gate_state_classifier_state.json", {})
    reports = state.get("reports", []) if isinstance(state, dict) else []
    out = []
    for r in reports:
        pair = norm(r.get("pair"))
        side = norm(r.get("side")).upper()
        if pair == "UNKNOWN" or side not in {"LONG", "SHORT"}:
            continue
        out.append(r)
    return out


def load_prev_state(runtime: Path) -> Dict[str, Any]:
    return load_json(runtime / "revo_f3g_watch_expiry_state.json", {})


def previous_records_map(prev: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    rows = prev.get("watch_records", []) if isinstance(prev, dict) else []
    out = {}
    for r in rows:
        pair = norm(r.get("pair"))
        side = norm(r.get("side")).upper()
        if pair != "UNKNOWN" and side in {"LONG", "SHORT"}:
            out[key_for(pair, side)] = r
    return out


def classify_watch_record(
    report: Dict[str, Any],
    previous: Optional[Dict[str, Any]],
    now_dt: datetime,
    max_location_age_min: float,
    max_trigger_age_min: float,
    max_entry_ready_age_min: float,
) -> Dict[str, Any]:
    pair = norm(report.get("pair"))
    side = norm(report.get("side")).upper()
    k = key_for(pair, side)

    latest_state = norm(report.get("latest_state"))
    recommended_action = norm(report.get("recommended_action"))
    latest_ts = norm(report.get("latest_ts"))
    latest_candle = norm(report.get("latest_candle"))

    prev_created = None
    prev_first_reason = "NEW_RECORD"
    prev_status = "NONE"

    if previous:
        prev_created = parse_dt(previous.get("created_ts"))
        prev_first_reason = norm(previous.get("created_reason"))
        prev_status = norm(previous.get("watch_status"))

    created_dt = prev_created or now_dt
    created_ts = created_dt.isoformat()
    age_min = minutes_between(created_dt, now_dt)

    latest_event_dt = parse_dt(latest_ts)
    latest_event_age_min = minutes_between(latest_event_dt, now_dt) if latest_event_dt else 0.0

    expire_reason = "NONE"
    watch_status = "UNKNOWN"
    next_action = "HOLD_AUDIT"

    if latest_state == "INVALIDATED_DIRECTION":
        watch_status = "EXPIRED"
        expire_reason = "DIRECTION_INVALIDATED"
        next_action = "REMOVE_FROM_FLOW_READY_WATCH_AUDIT"
    elif latest_state == "AVOID_TRAP":
        watch_status = "EXPIRED"
        expire_reason = "TRAP_RISK"
        next_action = "REMOVE_FROM_FLOW_READY_WATCH_AUDIT"
    elif latest_state == "CONTEXT_BLOCK":
        watch_status = "EXPIRED"
        expire_reason = "CONTEXT_BLOCK"
        next_action = "WAIT_CONTEXT_REPAIR_OR_NEXT_CYCLE_AUDIT"
    elif latest_state == "GEOMETRY_BLOCK":
        watch_status = "EXPIRED"
        expire_reason = "GEOMETRY_BLOCK"
        next_action = "DO_NOT_FORCE_ENTRY_AUDIT"
    elif latest_state == "NO_LATEST_GATE_EVENT":
        watch_status = "EXPIRED"
        expire_reason = "NO_LATEST_GATE_EVENT"
        next_action = "WAIT_NEW_TELEMETRY_AUDIT"
    elif latest_state == "WAIT_LOCATION":
        if age_min > max_location_age_min:
            watch_status = "EXPIRED"
            expire_reason = f"MAX_LOCATION_WATCH_AGE>{max_location_age_min}"
            next_action = "REMOVE_STALE_LOCATION_WATCH_AUDIT"
        else:
            watch_status = "ACTIVE_WAIT_LOCATION"
            expire_reason = "NONE"
            next_action = "KEEP_WATCH_UNTIL_LOCATION_VALID_AUDIT"
    elif latest_state == "WAIT_TRIGGER":
        if age_min > max_trigger_age_min:
            watch_status = "EXPIRED"
            expire_reason = f"MAX_TRIGGER_WATCH_AGE>{max_trigger_age_min}"
            next_action = "REMOVE_STALE_TRIGGER_WATCH_AUDIT"
        else:
            watch_status = "ACTIVE_WAIT_TRIGGER"
            expire_reason = "NONE"
            next_action = "KEEP_WATCH_UNTIL_TRIGGER_AUDIT"
    elif latest_state == "ENTRY_READY_SHADOW":
        if age_min > max_entry_ready_age_min:
            watch_status = "EXPIRED"
            expire_reason = f"MAX_ENTRY_READY_OBSERVE_AGE>{max_entry_ready_age_min}"
            next_action = "ENTRY_READY_STALE_OBSERVE_ONLY"
        else:
            watch_status = "ENTRY_READY_SHADOW_OBSERVE"
            expire_reason = "NONE"
            next_action = "OBSERVE_PAPER_OUTCOME_ONLY_AUDIT"
    elif latest_state == "SCORE_READY_GATE_DENY":
        watch_status = "ACTIVE_GATE_DENY_AUDIT"
        expire_reason = "NONE"
        next_action = "AUDIT_PRECISE_GATE_BLOCK"
    elif latest_state == "FLOW_DIRECTION_BLOCK":
        if age_min > max_trigger_age_min:
            watch_status = "EXPIRED"
            expire_reason = f"PERSISTENT_FLOW_DIRECTION_BLOCK>{max_trigger_age_min}"
            next_action = "REMOVE_PERSISTENT_DIRECTION_BLOCK_AUDIT"
        else:
            watch_status = "ACTIVE_DIRECTION_RECHECK"
            expire_reason = "NONE"
            next_action = "RECHECK_DIRECTION_NEXT_CYCLE_AUDIT"
    else:
        watch_status = "HOLD_UNKNOWN_STATE"
        expire_reason = "UNKNOWN_STATE"
        next_action = "HOLD_NO_PATCH"

    direction_opposite = int(report.get("direction_opposite") or 0)
    direction_aligned = int(report.get("direction_aligned") or 0)

    return {
        "key": k,
        "pair": pair,
        "side": side,
        "created_ts": created_ts,
        "created_reason": prev_first_reason if previous else latest_state,
        "last_seen_ts": utc_now(),
        "latest_gate_ts": latest_ts,
        "latest_candle": latest_candle,
        "age_min": age_min,
        "latest_event_age_min": latest_event_age_min,
        "previous_status": prev_status,
        "latest_state": latest_state,
        "recommended_action_from_f3f": recommended_action,
        "watch_status": watch_status,
        "next_action": next_action,
        "expire_reason": expire_reason,
        "f3d_score": report.get("f3d_score"),
        "f3d_max_score": report.get("f3d_max_score"),
        "f3d_ratio": as_float(report.get("f3d_ratio")),
        "latest_reason": norm(report.get("latest_reason")),
        "reason_class": norm(report.get("reason_class")),
        "regime": norm(report.get("regime")),
        "pd_zone": norm(report.get("pd_zone")),
        "location_state": norm(report.get("location_state")),
        "direction_engine": norm(report.get("direction_engine")),
        "flow_direction": norm(report.get("flow_direction")),
        "desired_direction": norm(report.get("desired_direction")),
        "direction_aligned": direction_aligned,
        "direction_opposite": direction_opposite,
        "final_allow": int(report.get("final_allow") or 0),
        "gate_allow": int(report.get("gate_allow") or 0),
        "score_allow": int(report.get("score_allow") or 0),
        "shadow_grade": norm(report.get("shadow_grade")),
        "shadow_score": as_float(report.get("shadow_score")),
        "recent_reason_classes": report.get("recent_reason_classes", []),
        "recent_final_reasons": report.get("recent_final_reasons", []),
        "recent_directions": report.get("recent_directions", []),
        "recent_zones": report.get("recent_zones", []),
        "behavior_change": "NONE",
        "entry_gate_change": "NONE",
        "risk_change": "NONE",
    }


def classify_missing_previous(
    previous: Dict[str, Any],
    now_dt: datetime,
    missing_expire_min: float,
) -> Dict[str, Any]:
    created_dt = parse_dt(previous.get("created_ts")) or now_dt
    last_seen_dt = parse_dt(previous.get("last_seen_ts")) or created_dt
    missing_age_min = minutes_between(last_seen_dt, now_dt)
    age_min = minutes_between(created_dt, now_dt)

    if missing_age_min > missing_expire_min:
        watch_status = "EXPIRED"
        expire_reason = f"MISSING_FROM_F3F>{missing_expire_min}"
        next_action = "REMOVE_MISSING_WATCH_AUDIT"
    else:
        watch_status = "MISSING_FROM_LATEST_F3F_RECHECK"
        expire_reason = "NONE"
        next_action = "RECHECK_NEXT_F3F_CYCLE_AUDIT"

    row = dict(previous)
    row.update({
        "age_min": age_min,
        "missing_age_min": missing_age_min,
        "previous_status": norm(previous.get("watch_status")),
        "latest_state": "MISSING_FROM_LATEST_F3F",
        "watch_status": watch_status,
        "next_action": next_action,
        "expire_reason": expire_reason,
        "behavior_change": "NONE",
        "entry_gate_change": "NONE",
        "risk_change": "NONE",
    })
    return row


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runtime-dir", default="user_data/revo_alpha/runtime/bybit")
    ap.add_argument("--max-location-age-min", type=float, default=180.0)
    ap.add_argument("--max-trigger-age-min", type=float, default=60.0)
    ap.add_argument("--max-entry-ready-age-min", type=float, default=30.0)
    ap.add_argument("--missing-expire-min", type=float, default=30.0)
    args = ap.parse_args()

    runtime = Path(args.runtime_dir)
    runtime.mkdir(parents=True, exist_ok=True)

    now_dt = utc_now_dt()
    reports = load_f3f_reports(runtime)
    prev = load_prev_state(runtime)
    prev_map = previous_records_map(prev)

    current_records: List[Dict[str, Any]] = []
    current_keys = set()

    for r in reports:
        pair = norm(r.get("pair"))
        side = norm(r.get("side")).upper()
        k = key_for(pair, side)
        current_keys.add(k)
        rec = classify_watch_record(
            report=r,
            previous=prev_map.get(k),
            now_dt=now_dt,
            max_location_age_min=args.max_location_age_min,
            max_trigger_age_min=args.max_trigger_age_min,
            max_entry_ready_age_min=args.max_entry_ready_age_min,
        )
        current_records.append(rec)

    missing_previous_records = []
    for k, old in prev_map.items():
        old_status = norm(old.get("watch_status"))
        if k not in current_keys and old_status not in {"EXPIRED"}:
            missing_previous_records.append(
                classify_missing_previous(old, now_dt, args.missing_expire_min)
            )

    watch_records = current_records + missing_previous_records

    status_counts = Counter(x["watch_status"] for x in watch_records)
    action_counts = Counter(x["next_action"] for x in watch_records)
    expire_counts = Counter(x["expire_reason"] for x in watch_records)
    latest_state_counts = Counter(x["latest_state"] for x in watch_records)
    pair_state = [f"{x['pair']}|{x['side']}={x['watch_status']}" for x in watch_records]

    active = [x for x in watch_records if str(x["watch_status"]).startswith("ACTIVE") or x["watch_status"] == "ENTRY_READY_SHADOW_OBSERVE"]
    wait_location = [x for x in watch_records if x["watch_status"] == "ACTIVE_WAIT_LOCATION"]
    wait_trigger = [x for x in watch_records if x["watch_status"] == "ACTIVE_WAIT_TRIGGER"]
    entry_ready = [x for x in watch_records if x["watch_status"] == "ENTRY_READY_SHADOW_OBSERVE"]
    expired = [x for x in watch_records if x["watch_status"] == "EXPIRED"]
    recheck = [x for x in watch_records if "RECHECK" in x["watch_status"] or "RECHECK" in x["next_action"]]

    if entry_ready:
        decision = "F3G_ENTRY_READY_SHADOW_OBSERVE_ONLY"
    elif wait_location or wait_trigger:
        decision = "F3G_ACTIVE_WATCH_LIFECYCLE_READY"
    elif expired and len(expired) == len(watch_records):
        decision = "F3G_ALL_CANDIDATES_EXPIRED"
    elif watch_records:
        decision = "F3G_WATCH_RECHECK_REQUIRED"
    else:
        decision = "F3G_NO_WATCH_RECORDS"

    payload = {
        "event": "F3G_WATCH_EXPIRY_AND_RECHECK",
        "generated_at": utc_now(),
        "runtime_dir": str(runtime),
        "config": {
            "max_location_age_min": args.max_location_age_min,
            "max_trigger_age_min": args.max_trigger_age_min,
            "max_entry_ready_age_min": args.max_entry_ready_age_min,
            "missing_expire_min": args.missing_expire_min,
        },
        "input_f3f_reports": len(reports),
        "previous_records": len(prev_map),
        "watch_records_count": len(watch_records),
        "pair_state": pair_state,
        "status_counts": status_counts.most_common(),
        "action_counts": action_counts.most_common(),
        "expire_counts": expire_counts.most_common(),
        "latest_state_counts": latest_state_counts.most_common(),
        "active": active,
        "wait_location": wait_location,
        "wait_trigger": wait_trigger,
        "entry_ready": entry_ready,
        "expired": expired,
        "recheck": recheck,
        "watch_records": watch_records,
        "decision": decision,
        "behavior_change": "NONE",
        "entry_gate_change": "NONE",
        "risk_change": "NONE",
        "watch_model_only": True,
    }

    out_state = runtime / "revo_f3g_watch_expiry_state.json"
    out_compact_runtime = runtime / "F3G_WATCH_EXPIRY_AND_RECHECK_COMPACT.txt"
    out_compact_root = Path("F3G_WATCH_EXPIRY_AND_RECHECK_COMPACT.txt")

    out_state.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    lines = []
    lines.append("F3G_WATCH_EXPIRY_AND_RECHECK_COMPACT")
    lines.append(f"generated_at={payload['generated_at']}")
    lines.append(f"runtime_dir={runtime}")
    lines.append("source=F3F_LATEST_GATE_STATE_CLASSIFIER")
    lines.append("behavior_change=NONE")
    lines.append("entry_gate_change=NONE")
    lines.append("risk_change=NONE")
    lines.append("watch_model_only=True")
    lines.append("")
    lines.append("CONFIG")
    lines.append(f"max_location_age_min={args.max_location_age_min}")
    lines.append(f"max_trigger_age_min={args.max_trigger_age_min}")
    lines.append(f"max_entry_ready_age_min={args.max_entry_ready_age_min}")
    lines.append(f"missing_expire_min={args.missing_expire_min}")
    lines.append("")
    lines.append("COUNTS")
    lines.append(f"input_f3f_reports={len(reports)}")
    lines.append(f"previous_records={len(prev_map)}")
    lines.append(f"watch_records_count={len(watch_records)}")
    lines.append(f"active_count={len(active)}")
    lines.append(f"wait_location_count={len(wait_location)}")
    lines.append(f"wait_trigger_count={len(wait_trigger)}")
    lines.append(f"entry_ready_count={len(entry_ready)}")
    lines.append(f"expired_count={len(expired)}")
    lines.append(f"recheck_count={len(recheck)}")
    lines.append("")
    lines.append("WATCH_STATUS_COUNTS")
    for k, v in status_counts.most_common():
        lines.append(f"{k}={v}")
    lines.append("")
    lines.append("NEXT_ACTION_COUNTS")
    for k, v in action_counts.most_common():
        lines.append(f"{k}={v}")
    lines.append("")
    lines.append("EXPIRE_REASON_COUNTS")
    for k, v in expire_counts.most_common():
        lines.append(f"{k}={v}")
    lines.append("")
    lines.append("LATEST_STATE_COUNTS")
    for k, v in latest_state_counts.most_common():
        lines.append(f"{k}={v}")
    lines.append("")
    lines.append("WATCH_RECORD_DETAIL")
    for x in watch_records:
        lines.append(
            f"{x['pair']}|side={x['side']}|status={x['watch_status']}|action={x['next_action']}|"
            f"latest_state={x['latest_state']}|age_min={x['age_min']}|latest_gate_ts={x.get('latest_gate_ts')}|"
            f"expire_reason={x['expire_reason']}|f3d={x.get('f3d_score')}/{x.get('f3d_max_score')}|ratio={x.get('f3d_ratio')}|"
            f"reason={x.get('latest_reason')}|class={x.get('reason_class')}|"
            f"regime={x.get('regime')}|zone={x.get('pd_zone')}|location={x.get('location_state')}|"
            f"direction={x.get('direction_engine')}|flow_direction={x.get('flow_direction')}|desired={x.get('desired_direction')}|"
            f"dir_aligned={x.get('direction_aligned')}|dir_opposite={x.get('direction_opposite')}|"
            f"final_allow={x.get('final_allow')}|gate_allow={x.get('gate_allow')}|score_allow={x.get('score_allow')}"
        )
    lines.append("")
    lines.append("ACTIVE_WAIT_LOCATION")
    for x in wait_location:
        lines.append(
            f"{x['pair']}|side={x['side']}|age_min={x['age_min']}|zone={x.get('pd_zone')}|"
            f"direction={x.get('direction_engine')}|flow_direction={x.get('flow_direction')}|reason={x.get('latest_reason')}"
        )
    lines.append("")
    lines.append("ACTIVE_WAIT_TRIGGER")
    for x in wait_trigger:
        lines.append(
            f"{x['pair']}|side={x['side']}|age_min={x['age_min']}|zone={x.get('pd_zone')}|"
            f"direction={x.get('direction_engine')}|flow_direction={x.get('flow_direction')}|reason={x.get('latest_reason')}"
        )
    lines.append("")
    lines.append("ENTRY_READY_SHADOW_OBSERVE")
    for x in entry_ready:
        lines.append(f"{x['pair']}|side={x['side']}|age_min={x['age_min']}|latest_gate_ts={x.get('latest_gate_ts')}")
    lines.append("")
    lines.append("EXPIRED_RECORDS")
    for x in expired:
        lines.append(
            f"{x['pair']}|side={x['side']}|expire_reason={x['expire_reason']}|"
            f"latest_state={x['latest_state']}|direction={x.get('direction_engine')}|flow_direction={x.get('flow_direction')}|"
            f"desired={x.get('desired_direction')}|reason={x.get('latest_reason')}"
        )
    lines.append("")
    lines.append("DECISION")
    lines.append(decision)
    lines.append("NO_ENTRY_PROMOTION_FROM_THIS_REPORT_ALONE")
    lines.append("NO_GATE_LOOSEN")
    lines.append("NO_RISK_INCREASE")
    lines.append("WATCH_LIFECYCLE_AUDIT_READY")
    lines.append("NEXT_STEP: RUN_F3G_PERIODICALLY_OR_BUILD_F3H_WATCH_TO_SHADOW_ENTRY_AUDIT")
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
