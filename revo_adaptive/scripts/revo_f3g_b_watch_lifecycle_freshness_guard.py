#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from datetime import datetime, timezone
from collections import Counter
from typing import Any, Dict, List, Optional


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


def freshness_limit_for(record: Dict[str, Any], args: argparse.Namespace) -> float:
    status = norm(record.get("watch_status"))

    if status == "ACTIVE_WAIT_LOCATION":
        return args.max_location_gate_age_min
    if status == "ACTIVE_WAIT_TRIGGER":
        return args.max_trigger_gate_age_min
    if status == "ENTRY_READY_SHADOW_OBSERVE":
        return args.max_entry_ready_gate_age_min
    if status in {"ACTIVE_DIRECTION_RECHECK", "ACTIVE_GATE_DENY_AUDIT"}:
        return args.max_recheck_gate_age_min

    return args.max_generic_gate_age_min


def classify_record(record: Dict[str, Any], now_dt: datetime, args: argparse.Namespace) -> Dict[str, Any]:
    r = dict(record)

    original_status = norm(r.get("watch_status"))
    latest_state = norm(r.get("latest_state"))
    latest_gate_ts = norm(r.get("latest_gate_ts"))
    latest_gate_dt = parse_dt(latest_gate_ts)

    latest_gate_age_min = minutes_between(latest_gate_dt, now_dt) if latest_gate_dt else None
    max_gate_age_min = freshness_limit_for(r, args)

    freshness_state = "UNKNOWN"
    freshness_action = "HOLD_AUDIT"
    freshness_reason = "UNKNOWN"

    guarded_status = original_status
    guarded_action = norm(r.get("next_action"))

    # Expired records remain expired. Freshness guard must not revive them.
    if original_status == "EXPIRED":
        freshness_state = "EXPIRED_RECORD_UNCHANGED"
        freshness_action = "KEEP_EXPIRED"
        freshness_reason = norm(r.get("expire_reason")) or "ALREADY_EXPIRED"

    elif latest_gate_dt is None:
        freshness_state = "MISSING_GATE_TIMESTAMP"
        guarded_status = "STALE_GATE_TELEMETRY_RECHECK"
        guarded_action = "RECHECK_GATE_TELEMETRY_NEXT_CYCLE_AUDIT"
        freshness_action = guarded_action
        freshness_reason = "LATEST_GATE_TS_MISSING"

    elif latest_gate_age_min is not None and latest_gate_age_min > args.stale_expire_gate_age_min:
        freshness_state = "STALE_GATE_TELEMETRY_EXPIRED"
        guarded_status = "EXPIRED"
        guarded_action = "REMOVE_STALE_GATE_TELEMETRY_WATCH_AUDIT"
        freshness_action = guarded_action
        freshness_reason = f"LATEST_GATE_AGE>{args.stale_expire_gate_age_min}"

    elif latest_gate_age_min is not None and latest_gate_age_min > max_gate_age_min:
        freshness_state = "STALE_GATE_TELEMETRY_RECHECK"
        guarded_status = "STALE_GATE_TELEMETRY_RECHECK"
        guarded_action = "RECHECK_GATE_TELEMETRY_NEXT_CYCLE_AUDIT"
        freshness_action = guarded_action
        freshness_reason = f"LATEST_GATE_AGE>{max_gate_age_min}"

    else:
        freshness_state = "FRESH_GATE_TELEMETRY"
        freshness_action = "KEEP_ORIGINAL_WATCH_STATUS"
        freshness_reason = "LATEST_GATE_FRESH"

    r.update({
        "original_watch_status": original_status,
        "guarded_watch_status": guarded_status,
        "original_next_action": norm(record.get("next_action")),
        "guarded_next_action": guarded_action,
        "freshness_state": freshness_state,
        "freshness_action": freshness_action,
        "freshness_reason": freshness_reason,
        "latest_gate_age_min": latest_gate_age_min,
        "max_gate_age_min": max_gate_age_min,
        "stale_expire_gate_age_min": args.stale_expire_gate_age_min,
        "latest_state": latest_state,
        "behavior_change": "NONE",
        "entry_gate_change": "NONE",
        "risk_change": "NONE",
    })

    return r


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runtime-dir", default="user_data/revo_alpha/runtime/bybit")
    ap.add_argument("--max-location-gate-age-min", type=float, default=30.0)
    ap.add_argument("--max-trigger-gate-age-min", type=float, default=15.0)
    ap.add_argument("--max-entry-ready-gate-age-min", type=float, default=10.0)
    ap.add_argument("--max-recheck-gate-age-min", type=float, default=15.0)
    ap.add_argument("--max-generic-gate-age-min", type=float, default=30.0)
    ap.add_argument("--stale-expire-gate-age-min", type=float, default=1440.0)
    args = ap.parse_args()

    runtime = Path(args.runtime_dir)

    f3g_path = runtime / "revo_f3g_watch_expiry_state.json"
    f3g = load_json(f3g_path, {})

    source_records = f3g.get("watch_records", []) if isinstance(f3g, dict) else []
    now_dt = utc_now_dt()

    guarded_records = [classify_record(x, now_dt, args) for x in source_records]

    original_status_counts = Counter(x["original_watch_status"] for x in guarded_records)
    guarded_status_counts = Counter(x["guarded_watch_status"] for x in guarded_records)
    freshness_counts = Counter(x["freshness_state"] for x in guarded_records)
    action_counts = Counter(x["guarded_next_action"] for x in guarded_records)
    reason_counts = Counter(x["freshness_reason"] for x in guarded_records)

    fresh_records = [x for x in guarded_records if x["freshness_state"] == "FRESH_GATE_TELEMETRY"]
    stale_recheck = [x for x in guarded_records if x["freshness_state"] == "STALE_GATE_TELEMETRY_RECHECK"]
    stale_expired = [x for x in guarded_records if x["freshness_state"] == "STALE_GATE_TELEMETRY_EXPIRED"]
    expired_unchanged = [x for x in guarded_records if x["freshness_state"] == "EXPIRED_RECORD_UNCHANGED"]
    active_after_guard = [
        x for x in guarded_records
        if x["guarded_watch_status"] in {
            "ACTIVE_WAIT_LOCATION",
            "ACTIVE_WAIT_TRIGGER",
            "ENTRY_READY_SHADOW_OBSERVE",
            "ACTIVE_DIRECTION_RECHECK",
            "ACTIVE_GATE_DENY_AUDIT",
        }
    ]

    if not guarded_records:
        decision = "F3G_B_NO_WATCH_RECORDS"
    elif stale_recheck:
        decision = "F3G_B_STALE_GATE_TELEMETRY_RECHECK_REQUIRED"
    elif stale_expired:
        decision = "F3G_B_STALE_GATE_TELEMETRY_EXPIRED_EXISTS"
    elif active_after_guard:
        decision = "F3G_B_ACTIVE_WATCH_FRESHNESS_OK"
    else:
        decision = "F3G_B_NO_ACTIVE_WATCH_AFTER_FRESHNESS_GUARD"

    payload = {
        "event": "F3G_B_WATCH_LIFECYCLE_FRESHNESS_GUARD",
        "generated_at": utc_now(),
        "runtime_dir": str(runtime),
        "source_state": str(f3g_path),
        "config": {
            "max_location_gate_age_min": args.max_location_gate_age_min,
            "max_trigger_gate_age_min": args.max_trigger_gate_age_min,
            "max_entry_ready_gate_age_min": args.max_entry_ready_gate_age_min,
            "max_recheck_gate_age_min": args.max_recheck_gate_age_min,
            "max_generic_gate_age_min": args.max_generic_gate_age_min,
            "stale_expire_gate_age_min": args.stale_expire_gate_age_min,
        },
        "source_records": len(source_records),
        "guarded_records_count": len(guarded_records),
        "original_status_counts": original_status_counts.most_common(),
        "guarded_status_counts": guarded_status_counts.most_common(),
        "freshness_counts": freshness_counts.most_common(),
        "action_counts": action_counts.most_common(),
        "reason_counts": reason_counts.most_common(),
        "fresh_records": fresh_records,
        "stale_recheck": stale_recheck,
        "stale_expired": stale_expired,
        "expired_unchanged": expired_unchanged,
        "active_after_guard": active_after_guard,
        "guarded_records": guarded_records,
        "decision": decision,
        "behavior_change": "NONE",
        "entry_gate_change": "NONE",
        "risk_change": "NONE",
        "watch_model_only": True,
    }

    out_state = runtime / "revo_f3g_b_watch_lifecycle_freshness_guard_state.json"
    out_compact_runtime = runtime / "F3G_B_WATCH_LIFECYCLE_FRESHNESS_GUARD_COMPACT.txt"
    out_compact_root = Path("F3G_B_WATCH_LIFECYCLE_FRESHNESS_GUARD_COMPACT.txt")

    out_state.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    lines = []
    lines.append("F3G_B_WATCH_LIFECYCLE_FRESHNESS_GUARD_COMPACT")
    lines.append(f"generated_at={payload['generated_at']}")
    lines.append(f"runtime_dir={runtime}")
    lines.append(f"source_state={f3g_path}")
    lines.append("source=F3G_WATCH_EXPIRY_AND_RECHECK")
    lines.append("behavior_change=NONE")
    lines.append("entry_gate_change=NONE")
    lines.append("risk_change=NONE")
    lines.append("watch_model_only=True")
    lines.append("")
    lines.append("CONFIG")
    lines.append(f"max_location_gate_age_min={args.max_location_gate_age_min}")
    lines.append(f"max_trigger_gate_age_min={args.max_trigger_gate_age_min}")
    lines.append(f"max_entry_ready_gate_age_min={args.max_entry_ready_gate_age_min}")
    lines.append(f"max_recheck_gate_age_min={args.max_recheck_gate_age_min}")
    lines.append(f"max_generic_gate_age_min={args.max_generic_gate_age_min}")
    lines.append(f"stale_expire_gate_age_min={args.stale_expire_gate_age_min}")
    lines.append("")
    lines.append("COUNTS")
    lines.append(f"source_records={len(source_records)}")
    lines.append(f"guarded_records_count={len(guarded_records)}")
    lines.append(f"fresh_count={len(fresh_records)}")
    lines.append(f"stale_recheck_count={len(stale_recheck)}")
    lines.append(f"stale_expired_count={len(stale_expired)}")
    lines.append(f"expired_unchanged_count={len(expired_unchanged)}")
    lines.append(f"active_after_guard_count={len(active_after_guard)}")
    lines.append("")
    lines.append("ORIGINAL_STATUS_COUNTS")
    for k, v in original_status_counts.most_common():
        lines.append(f"{k}={v}")
    lines.append("")
    lines.append("GUARDED_STATUS_COUNTS")
    for k, v in guarded_status_counts.most_common():
        lines.append(f"{k}={v}")
    lines.append("")
    lines.append("FRESHNESS_COUNTS")
    for k, v in freshness_counts.most_common():
        lines.append(f"{k}={v}")
    lines.append("")
    lines.append("GUARDED_ACTION_COUNTS")
    for k, v in action_counts.most_common():
        lines.append(f"{k}={v}")
    lines.append("")
    lines.append("FRESHNESS_REASON_COUNTS")
    for k, v in reason_counts.most_common():
        lines.append(f"{k}={v}")
    lines.append("")
    lines.append("GUARDED_RECORD_DETAIL")
    for x in guarded_records:
        lines.append(
            f"{x['pair']}|side={x['side']}|original={x['original_watch_status']}|guarded={x['guarded_watch_status']}|"
            f"freshness={x['freshness_state']}|action={x['guarded_next_action']}|"
            f"latest_gate_ts={x.get('latest_gate_ts')}|latest_gate_age_min={x.get('latest_gate_age_min')}|"
            f"max_gate_age_min={x.get('max_gate_age_min')}|freshness_reason={x.get('freshness_reason')}|"
            f"latest_state={x.get('latest_state')}|reason={x.get('latest_reason')}|"
            f"direction={x.get('direction_engine')}|flow_direction={x.get('flow_direction')}|desired={x.get('desired_direction')}|"
            f"zone={x.get('pd_zone')}|location={x.get('location_state')}"
        )
    lines.append("")
    lines.append("STALE_RECHECK")
    for x in stale_recheck:
        lines.append(
            f"{x['pair']}|side={x['side']}|age={x.get('latest_gate_age_min')}|limit={x.get('max_gate_age_min')}|"
            f"original={x['original_watch_status']}|latest_state={x.get('latest_state')}|reason={x.get('latest_reason')}"
        )
    lines.append("")
    lines.append("ACTIVE_AFTER_GUARD")
    for x in active_after_guard:
        lines.append(
            f"{x['pair']}|side={x['side']}|status={x['guarded_watch_status']}|"
            f"age={x.get('latest_gate_age_min')}|limit={x.get('max_gate_age_min')}|reason={x.get('latest_reason')}"
        )
    lines.append("")
    lines.append("EXPIRED_UNCHANGED")
    for x in expired_unchanged:
        lines.append(
            f"{x['pair']}|side={x['side']}|expire_reason={x.get('expire_reason')}|"
            f"latest_state={x.get('latest_state')}|reason={x.get('latest_reason')}"
        )
    lines.append("")
    lines.append("DECISION")
    lines.append(decision)
    lines.append("NO_ENTRY_PROMOTION_FROM_THIS_REPORT_ALONE")
    lines.append("NO_GATE_LOOSEN")
    lines.append("NO_RISK_INCREASE")
    lines.append("FRESHNESS_GUARD_AUDIT_READY")
    lines.append("NEXT_STEP: RERUN_F3A_TO_F3G_SEQUENCE_OR_BUILD_F3H_WATCH_TO_SHADOW_ENTRY_AUDIT_ONLY_AFTER_FRESH_GATE")
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
