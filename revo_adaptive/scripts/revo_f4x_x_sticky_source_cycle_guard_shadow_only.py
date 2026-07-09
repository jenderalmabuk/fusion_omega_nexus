#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path
from datetime import datetime, timezone
from collections import Counter
from typing import Any, Dict, List, Optional


HEARTBEAT_FILE = "revo_gate_heartbeat_events.jsonl"
F3C_FILE = "revo_f3c_event_aligned_flow_snapshots.jsonl"


def now_utc() -> str:
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


def parse_dt(v: Any) -> Optional[datetime]:
    s = norm(v)
    if s == "UNKNOWN":
        return None
    s = s.replace("Z", "+00:00")
    try:
        d = datetime.fromisoformat(s)
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d
    except Exception:
        return None


def parse_cycle_id(v: Any) -> Optional[datetime]:
    s = norm(v)
    m = re.search(r"(\d{8}T\d{6})Z?", s)
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)
    except Exception:
        return None


def event_time(e: Dict[str, Any]) -> Optional[datetime]:
    for k in ("candle", "timestamp", "generated_at", "latest_ts", "sqlite_cycle_ts", "time"):
        if k in e:
            d = parse_dt(e.get(k))
            if d:
                return d
    return None


def dt_text(d: Optional[datetime]) -> str:
    return d.isoformat() if d else "UNKNOWN"


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return default


def write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def tmux_alive(name: str) -> bool:
    try:
        out = subprocess.check_output(["tmux", "list-sessions"], stderr=subprocess.DEVNULL).decode()
        return name in out
    except Exception:
        return False


def load_jsonl(path: Path, max_lines: int) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return []

    out = []
    for line in lines[-max_lines:]:
        try:
            obj = json.loads(line)
        except Exception:
            continue
        if isinstance(obj, dict):
            obj["_source_file"] = path.name
            out.append(obj)
    return out


def latest_by_pair(events: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for e in events:
        pair = norm(e.get("pair"))
        if pair == "UNKNOWN":
            continue
        t = event_time(e)
        old = out.get(pair)
        old_t = event_time(old) if old else None
        if old is None or (t and old_t and t >= old_t) or (t and old_t is None):
            out[pair] = e
    return out


def pair_cycle_counts(events: List[Dict[str, Any]]) -> Dict[str, Counter]:
    out: Dict[str, Counter] = {}
    for e in events:
        pair = norm(e.get("pair"))
        if pair == "UNKNOWN":
            continue
        out.setdefault(pair, Counter())[norm(e.get("cycle_id"))] += 1
    return out


def side_from_direction(direction: str) -> str:
    d = direction.upper()
    if d == "LONG_ONLY":
        return "LONG"
    if d == "SHORT_ONLY":
        return "SHORT"
    return "UNKNOWN"


def opposite_side(side: str) -> str:
    if side == "LONG":
        return "SHORT"
    if side == "SHORT":
        return "LONG"
    return "UNKNOWN"


def final_reason_for_side(e: Dict[str, Any], side: str) -> str:
    if side == "LONG":
        return norm(e.get("final_reason_long"))
    if side == "SHORT":
        return norm(e.get("final_reason_short"))
    return "UNKNOWN"


def final_allow_for_side(e: Dict[str, Any], side: str) -> str:
    if side == "LONG":
        return norm(e.get("final_allow_long"))
    if side == "SHORT":
        return norm(e.get("final_allow_short"))
    return "UNKNOWN"


def classify_sticky(
    hb: Dict[str, Any],
    f3c: Optional[Dict[str, Any]],
    cycle_count: int,
    max_cycle_drift_sec: int,
    min_repeat_count: int,
) -> Dict[str, Any]:
    pair = norm(hb.get("pair"))
    hb_time = event_time(hb)
    cycle_id = norm(hb.get("cycle_id"))
    cycle_time = parse_cycle_id(cycle_id)
    drift = abs((hb_time - cycle_time).total_seconds()) if hb_time and cycle_time else None

    direction = norm(hb.get("direction_engine") or hb.get("flow_direction")).upper()
    flow_direction = norm(hb.get("flow_direction")).upper()
    sticky_status = norm(hb.get("sticky_status")).upper()
    sticky_direction = norm(hb.get("sticky_current_direction")).upper()
    sticky_age = as_float(hb.get("sticky_age_sec"))
    sticky_exp = as_float(hb.get("sticky_expires_in_sec"))

    stale_cycle = drift is not None and drift > max_cycle_drift_sec
    repeated_cycle = cycle_count >= min_repeat_count
    age_reset = sticky_age <= 1.0
    ttl_reset = sticky_exp >= 1790.0
    active_actionable = sticky_status == "ACTIVE_ACTIONABLE" and sticky_exp > 0
    cycle_missing = cycle_id in ("UNKNOWN", "NONE", "")

    sticky_side = side_from_direction(sticky_direction if sticky_direction != "UNKNOWN" else direction)

    f3c_time = event_time(f3c) if f3c else None
    f3c_bias = norm(f3c.get("primary_bias")) if f3c else "UNKNOWN"
    f3c_newer_than_cycle = bool(f3c_time and cycle_time and f3c_time > cycle_time)

    guard_state = "NO_SHADOW_GUARD"
    shadow_action = "KEEP_AS_IS"
    reason = "NOT_STALE_ACTIVE_STICKY"

    if active_actionable and stale_cycle and repeated_cycle and age_reset and ttl_reset:
        guard_state = "STALE_STICKY_REFRESH_RESET_CONFIRMED"
        shadow_action = "DOWNGRADE_ACTIVE_ACTIONABLE_TO_STALE_STICKY_SHADOW"
        reason = "ACTIVE_ACTIONABLE_STICKY_REPUBLISHED_WITH_STALE_CYCLE_AND_RESET_TTL"
    elif active_actionable and stale_cycle:
        guard_state = "ACTIVE_STICKY_STALE_CYCLE"
        shadow_action = "MARK_STALE_STICKY_SHADOW"
        reason = "ACTIVE_ACTIONABLE_STICKY_HAS_STALE_CYCLE"
    elif active_actionable and cycle_missing:
        guard_state = "ACTIVE_STICKY_MISSING_CYCLE"
        shadow_action = "MARK_UNVERIFIED_STICKY_SHADOW"
        reason = "ACTIVE_ACTIONABLE_STICKY_MISSING_CYCLE_ID"
    else:
        guard_state = "NO_STALE_STICKY_BLOCK"
        shadow_action = "KEEP_AS_IS"
        reason = "STICKY_NOT_STALE_OR_NOT_ACTIVE_ACTIONABLE"

    return {
        "pair": pair,
        "sticky_side": sticky_side,
        "opposite_side": opposite_side(sticky_side),
        "heartbeat_time": dt_text(hb_time),
        "cycle_id": cycle_id,
        "cycle_time": dt_text(cycle_time),
        "cycle_drift_sec": drift,
        "cycle_drift_allowed_sec": max_cycle_drift_sec,
        "cycle_repeat_count": cycle_count,
        "direction_engine": norm(hb.get("direction_engine")),
        "flow_direction": norm(hb.get("flow_direction")),
        "flow_quadrant": norm(hb.get("flow_quadrant")),
        "flow_strength": norm(hb.get("flow_strength")),
        "sticky_status": sticky_status,
        "sticky_current_direction": sticky_direction,
        "sticky_current_quadrant": norm(hb.get("sticky_current_quadrant")),
        "sticky_current_strength": norm(hb.get("sticky_current_strength")),
        "sticky_age_sec": sticky_age,
        "sticky_expires_in_sec": sticky_exp,
        "active_actionable": active_actionable,
        "stale_cycle": stale_cycle,
        "repeated_cycle": repeated_cycle,
        "age_reset": age_reset,
        "ttl_reset": ttl_reset,
        "f3c_time": dt_text(f3c_time),
        "f3c_bias": f3c_bias,
        "f3c_newer_than_cycle": f3c_newer_than_cycle,
        "final_allow_long": norm(hb.get("final_allow_long")),
        "final_reason_long": norm(hb.get("final_reason_long")),
        "final_allow_short": norm(hb.get("final_allow_short")),
        "final_reason_short": norm(hb.get("final_reason_short")),
        "guard_state": guard_state,
        "shadow_action": shadow_action,
        "reason": reason,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runtime-dir", default="/home/fusion_omega/revo_adaptive/user_data/revo_alpha/runtime/bybit")
    ap.add_argument("--max-lines", type=int, default=220000)
    ap.add_argument("--max-cycle-drift-sec", type=int, default=1800)
    ap.add_argument("--min-repeat-count", type=int, default=3)
    ap.add_argument("--priority-pair", default="AAVE/USDT:USDT")
    args = ap.parse_args()

    runtime = Path(args.runtime_dir)

    hb_events = [
        e for e in load_jsonl(runtime / HEARTBEAT_FILE, args.max_lines)
        if norm(e.get("event")) == "GATE_HEARTBEAT"
    ]
    f3c_events = load_jsonl(runtime / F3C_FILE, args.max_lines)

    latest_hb = latest_by_pair(hb_events)
    latest_f3c = latest_by_pair(f3c_events)
    cycles = pair_cycle_counts(hb_events)

    rows = []
    for pair, hb in latest_hb.items():
        cid = norm(hb.get("cycle_id"))
        repeat_count = cycles.get(pair, Counter()).get(cid, 0)
        rows.append(
            classify_sticky(
                hb=hb,
                f3c=latest_f3c.get(pair),
                cycle_count=repeat_count,
                max_cycle_drift_sec=args.max_cycle_drift_sec,
                min_repeat_count=args.min_repeat_count,
            )
        )

    rows.sort(
        key=lambda r: (
            0 if r["pair"] == args.priority_pair else 1,
            0 if r["guard_state"] == "STALE_STICKY_REFRESH_RESET_CONFIRMED" else 1,
            -(r["cycle_drift_sec"] or 0),
        )
    )

    guard_rows = [r for r in rows if r["shadow_action"] != "KEEP_AS_IS"]
    confirmed = [r for r in rows if r["guard_state"] == "STALE_STICKY_REFRESH_RESET_CONFIRMED"]
    priority = [r for r in rows if r["pair"] == args.priority_pair]

    state_counts = Counter(r["guard_state"] for r in rows)
    action_counts = Counter(r["shadow_action"] for r in rows)
    sticky_side_counts = Counter(r["sticky_side"] for r in rows)

    if any(r["pair"] == args.priority_pair and r["guard_state"] == "STALE_STICKY_REFRESH_RESET_CONFIRMED" for r in rows):
        final_decision = "F4X_X_AAVE_STALE_STICKY_GUARD_SHADOW_READY"
    elif confirmed:
        final_decision = "F4X_X_STALE_STICKY_GUARD_SHADOW_READY"
    elif guard_rows:
        final_decision = "F4X_X_STICKY_STALENESS_RISK_FOUND"
    else:
        final_decision = "F4X_X_NO_STICKY_GUARD_ACTION"

    k_active = read_json(runtime / "F4X_K_PAPER_BRIDGE_ACTIVE_SIGNAL.json", {})
    l_full = read_json(runtime / "F4X_L_PAPER_BRIDGE_EXECUTION_FULL.json", {})

    payload = {
        "event": "F4X_X_STICKY_SOURCE_CYCLE_GUARD_SHADOW_ONLY",
        "generated_at": now_utc(),
        "runtime_dir": str(runtime),
        "final_decision": final_decision,
        "parameters": {
            "max_cycle_drift_sec": args.max_cycle_drift_sec,
            "min_repeat_count": args.min_repeat_count,
            "priority_pair": args.priority_pair,
        },
        "counts": {
            "latest_heartbeat_pair_count": len(latest_hb),
            "f3c_pair_count": len(latest_f3c),
            "row_count": len(rows),
            "guard_row_count": len(guard_rows),
            "confirmed_stale_refresh_count": len(confirmed),
            "priority_pair_row_count": len(priority),
            "k_has_order_intent": bool(k_active.get("has_order_intent")) if isinstance(k_active, dict) else False,
            "l_decision": l_full.get("decision") if isinstance(l_full, dict) else "UNKNOWN",
        },
        "state_counts": state_counts.most_common(),
        "action_counts": action_counts.most_common(),
        "sticky_side_counts": sticky_side_counts.most_common(),
        "guard_rows": guard_rows,
        "all_rows": rows,
        "tmux_state": {
            "f4x_l_paper_execution_dryrun": tmux_alive("f4x_l_paper_execution_dryrun"),
            "f4x_o4_snapshot_guard": tmux_alive("f4x_o4_snapshot_guard"),
            "f4x_p_entry_blocker_conveyor": tmux_alive("f4x_p_entry_blocker_conveyor"),
            "f4x_q_latest_source_attribution": tmux_alive("f4x_q_latest_source_attribution"),
            "f4x_r_flow_direction_trace": tmux_alive("f4x_r_flow_direction_trace"),
            "f4x_s_side_specific_selector_trace": tmux_alive("f4x_s_side_specific_selector_trace"),
            "f4x_t_flow_component_trace": tmux_alive("f4x_t_flow_component_trace"),
            "f4x_u_signal_execution_alignment": tmux_alive("f4x_u_signal_execution_alignment"),
            "f4x_v_sticky_source_priority": tmux_alive("f4x_v_sticky_source_priority"),
            "f4x_w_sticky_ttl_staleness": tmux_alive("f4x_w_sticky_ttl_staleness"),
        },
        "policy": {
            "paper_order": "HOLD",
            "live": "HOLD",
            "risk_up": "HOLD",
            "gate_loosen": "HOLD",
            "entry_from_watch_recheck_deny": "HOLD",
            "mode": "SHADOW_ONLY",
        },
    }

    full_path = runtime / "F4X_X_STICKY_SOURCE_CYCLE_GUARD_SHADOW_FULL.json"
    compact_path = runtime / "F4X_X_STICKY_SOURCE_CYCLE_GUARD_SHADOW_COMPACT.txt"
    shadow_path = runtime / "F4X_X_STICKY_SOURCE_CYCLE_GUARD_SHADOW_ACTIVE.json"

    write_json(full_path, payload)

    shadow_active = {
        "generated_at": payload["generated_at"],
        "mode": "STICKY_SOURCE_CYCLE_GUARD_SHADOW_ONLY",
        "active": bool(guard_rows),
        "paper_order_allowed": False,
        "live_allowed": False,
        "risk_up_allowed": False,
        "gate_loosen_allowed": False,
        "guard_rows": guard_rows,
    }
    write_json(shadow_path, shadow_active)

    lines = []
    lines.append("F4X_X_STICKY_SOURCE_CYCLE_GUARD_SHADOW_COMPACT")
    lines.append(f"generated_at={payload['generated_at']}")
    lines.append("mode=STICKY_SOURCE_CYCLE_GUARD_SHADOW_ONLY")
    lines.append("paper_order=HOLD")
    lines.append("live=HOLD")
    lines.append("risk_up=HOLD")
    lines.append("gate_loosen=HOLD")
    lines.append("entry_from_watch_recheck_deny=HOLD")
    lines.append("")
    lines.append("FINAL_DECISION")
    lines.append(f"final_decision={final_decision}")
    lines.append("")
    lines.append("COUNTS")
    for k, v in payload["counts"].items():
        lines.append(f"{k}={v}")
    lines.append("")
    lines.append("STATE_COUNTS")
    for k, v in payload["state_counts"]:
        lines.append(f"{k}={v}")
    lines.append("")
    lines.append("ACTION_COUNTS")
    for k, v in payload["action_counts"]:
        lines.append(f"{k}={v}")
    lines.append("")
    lines.append("GUARD_ROWS")
    for r in guard_rows[:40]:
        lines.append(
            f"{r['pair']}|sticky_side={r['sticky_side']}|guard={r['guard_state']}|action={r['shadow_action']}|"
            f"hb={r['heartbeat_time']}|cycle={r['cycle_id']}|drift={r['cycle_drift_sec']}|repeat={r['cycle_repeat_count']}|"
            f"sticky={r['sticky_status']}:{r['sticky_current_direction']}:{r['sticky_current_quadrant']}|"
            f"age={r['sticky_age_sec']}|expires={r['sticky_expires_in_sec']}|"
            f"f3c={r['f3c_time']}|f3c_bias={r['f3c_bias']}|reason={r['reason']}"
        )
    lines.append("")
    lines.append("PRIORITY_PAIR_DETAIL")
    for r in priority:
        lines.append(
            f"{r['pair']}|sticky_side={r['sticky_side']}|opposite_side={r['opposite_side']}|"
            f"guard={r['guard_state']}|action={r['shadow_action']}|reason={r['reason']}"
        )
        lines.append(
            f"  hb={r['heartbeat_time']}|cycle={r['cycle_id']}|cycle_time={r['cycle_time']}|"
            f"drift={r['cycle_drift_sec']}|allowed={r['cycle_drift_allowed_sec']}|repeat={r['cycle_repeat_count']}"
        )
        lines.append(
            f"  sticky={r['sticky_status']}:{r['sticky_current_direction']}:{r['sticky_current_quadrant']}|"
            f"age={r['sticky_age_sec']}|expires={r['sticky_expires_in_sec']}|"
            f"direction={r['direction_engine']}|flow={r['flow_direction']}|quadrant={r['flow_quadrant']}|strength={r['flow_strength']}"
        )
        lines.append(
            f"  final_long={r['final_allow_long']}:{r['final_reason_long']}|"
            f"final_short={r['final_allow_short']}:{r['final_reason_short']}|"
            f"f3c={r['f3c_time']}|bias={r['f3c_bias']}|f3c_newer={r['f3c_newer_than_cycle']}"
        )
    lines.append("")
    lines.append("SHADOW_RULES")
    lines.append("rule_1=do_not_treat_ACTIVE_ACTIONABLE_as_fresh_when_cycle_id_drift_exceeds_threshold")
    lines.append("rule_2=do_not_reset_sticky_age_to_zero_when_cycle_id_did_not_change")
    lines.append("rule_3=do_not_reset_sticky_expires_to_1800_when_cycle_id_did_not_change")
    lines.append("rule_4=if_f3c_latest_is_newer_than_sticky_cycle_then_revalidate_before_republish")
    lines.append("rule_5=shadow_label=STALE_STICKY_NOT_ACTIVE_ACTIONABLE")
    lines.append("")
    lines.append("TMUX_STATE")
    for k, v in payload["tmux_state"].items():
        lines.append(f"{k}={v}")
    lines.append("")
    lines.append("DECISION_POLICY")
    lines.append("This patch is shadow-only.")
    lines.append("It does not modify gate threshold.")
    lines.append("It does not create paper order.")
    lines.append("It only produces a guard file for stale sticky/source-cycle diagnosis.")
    lines.append("Next promotion requires validation that stale sticky rows are correctly downgraded in shadow.")
    lines.append("")
    lines.append("OUTPUT_FILES")
    lines.append(f"full_json={full_path}")
    lines.append(f"compact={compact_path}")
    lines.append(f"shadow_active={shadow_path}")

    text = "\n".join(lines) + "\n"
    compact_path.write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
