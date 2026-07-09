#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Dict, List


def utc_now() -> str:
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


def signal_key(s: Dict[str, Any]) -> str:
    return f"{norm(s.get('pair'))}|{norm(s.get('side')).upper()}"


def pair_key_from_count(k: str) -> str:
    return str(k).split("=", 1)[0].strip()


def build_persistence_map(aggregate: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}

    for k, v in aggregate.get("watch_pair_counts", []):
        out.setdefault(k, {})["watch_count"] = int(v)

    for k, v in aggregate.get("recheck_pair_counts", []):
        out.setdefault(k, {})["recheck_count"] = int(v)

    for k, v in aggregate.get("deny_pair_counts", []):
        out.setdefault(k, {})["deny_count"] = int(v)

    return out


def smc_clean_label(signal: Dict[str, Any]) -> str:
    smc = signal.get("smc", {}) if isinstance(signal.get("smc"), dict) else {}
    f3 = signal.get("f3", {}) if isinstance(signal.get("f3"), dict) else {}

    raw_grade = norm(smc.get("smc_grade"))
    status = norm(smc.get("smc_status")).upper()
    zone = norm(smc.get("pd_zone")).upper()
    location_reason = norm(smc.get("location_reason")).upper()
    side = norm(signal.get("side")).upper()

    hard_terms = ["GEOMETRY", "TPSL", "CONTEXT_BLOCK", "CONTRACT_MISS"]
    if any(t in status or t in location_reason for t in hard_terms):
        return "SMC_HARD_REJECT_GEOMETRY_OR_CONTEXT"

    if side == "LONG":
        if zone == "DISCOUNT":
            return "SMC_GOOD_LOCATION_LONG"
        if zone == "PREMIUM" or "LONG_IN_PREMIUM" in location_reason or "WAIT_LOCATION_PREMIUM_FOR_LONG" in status:
            return "SMC_WAIT_LOCATION_LONG_PREMIUM"
        if zone == "MID":
            return "SMC_MID_RANGE_WAIT_LONG"
    elif side == "SHORT":
        if zone == "PREMIUM":
            return "SMC_GOOD_LOCATION_SHORT"
        if zone == "DISCOUNT" or "SHORT_IN_DISCOUNT" in location_reason or "WAIT_LOCATION_DISCOUNT_FOR_SHORT" in status:
            return "SMC_WAIT_LOCATION_SHORT_DISCOUNT"
        if zone == "MID":
            return "SMC_MID_RANGE_WAIT_SHORT"

    if raw_grade in {"SMC_A", "SMC_A_PLUS"}:
        return "SMC_GOOD_LOCATION"
    if raw_grade == "SMC_B":
        return "SMC_WATCHABLE_LOCATION"
    if raw_grade == "SMC_REJECT":
        return "SMC_WAIT_OR_REJECT_UNCLEAR"
    if raw_grade == "SMC_C":
        return "SMC_UNKNOWN_OR_WEAK_LOCATION"

    latest_state = norm(f3.get("latest_state"))
    if latest_state == "WAIT_LOCATION":
        return "SMC_WAIT_LOCATION_FROM_F3"

    return "SMC_UNKNOWN"


def has_any(text_items: List[str], needles: List[str]) -> bool:
    text = " ".join(str(x).upper() for x in text_items)
    return any(n.upper() in text for n in needles)


def classify_lane(signal: Dict[str, Any], persistence: Dict[str, Any]) -> Dict[str, Any]:
    pair = norm(signal.get("pair"))
    side = norm(signal.get("side")).upper()
    action = norm(signal.get("paper_action"))
    reason = norm(signal.get("reason"))
    score = as_int(signal.get("score"))
    grade = norm(signal.get("final_grade"))

    cvdoi = signal.get("cvdoi", {}) if isinstance(signal.get("cvdoi"), dict) else {}
    cvd = signal.get("cvd", {}) if isinstance(signal.get("cvd"), dict) else {}
    trigger = signal.get("trigger", {}) if isinstance(signal.get("trigger"), dict) else {}
    btc = signal.get("btc", {}) if isinstance(signal.get("btc"), dict) else {}
    f3 = signal.get("f3", {}) if isinstance(signal.get("f3"), dict) else {}

    blockers = signal.get("blockers", [])
    if not isinstance(blockers, list):
        blockers = []
    hard_blockers = signal.get("hard_blockers", [])
    if not isinstance(hard_blockers, list):
        hard_blockers = []
    entry_blockers = signal.get("entry_blockers", [])
    if not isinstance(entry_blockers, list):
        entry_blockers = []
    supports = signal.get("supports", [])
    if not isinstance(supports, list):
        supports = []

    smc_label = smc_clean_label(signal)
    cvdoi_label = norm(cvdoi.get("cvdoi_label"))
    cvdoi_alignment = as_int(cvdoi.get("side_alignment"))
    cvd_status = norm(cvd.get("cvd_status"))
    cvd_label = norm(cvd.get("cvd_label"))
    trigger_status = norm(trigger.get("trigger_status"))
    btc_guard = norm(btc.get("btc_guard"))

    latest_state = norm(f3.get("latest_state"))
    guarded = norm(f3.get("guarded_watch_status"))
    freshness = norm(f3.get("freshness_state"))
    gate_allow = as_int(f3.get("gate_allow"))
    final_allow = as_int(f3.get("final_allow"))
    direction_opposite = as_int(f3.get("direction_opposite"))

    p_watch = as_int(persistence.get("watch_count"))
    p_recheck = as_int(persistence.get("recheck_count"))
    p_deny = as_int(persistence.get("deny_count"))
    persistence_score = p_watch * 3 + p_recheck - p_deny

    real_hard_reasons = []
    if hard_blockers:
        real_hard_reasons.extend(hard_blockers)

    if has_any(blockers, [
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
        "TRIGGER_DATA_MISSING",
        "TRIGGER_REJECTED",
    ]):
        real_hard_reasons.extend(blockers)

    if direction_opposite:
        real_hard_reasons.append("LATEST_DIRECTION_OPPOSITE")
    if freshness == "STALE_GATE_TELEMETRY_RECHECK":
        real_hard_reasons.append("FRESHNESS_STALE_RECHECK")
    if guarded == "EXPIRED":
        real_hard_reasons.append("F3G_B_EXPIRED")
    if latest_state in {"INVALIDATED_DIRECTION", "AVOID_TRAP", "CONTEXT_BLOCK", "GEOMETRY_BLOCK"}:
        real_hard_reasons.append(f"F3_LATEST_{latest_state}")

    cvdoi_good = cvdoi_alignment == 1 and cvdoi_label in {
        "BULLISH_CONTINUATION_STRONG",
        "BEARISH_CONTINUATION_STRONG",
        "SHORT_SQUEEZE",
        "LONG_UNWIND",
        "BULLISH_ABSORPTION_OR_SELL_PRESSURE_ABSORBED",
        "BEARISH_ABSORPTION_OR_BUY_PRESSURE_ABSORBED",
    }

    trigger_good = trigger_status in {"TRIGGER_CONFIRMED", "TRIGGER_WEAK"}
    smc_good = smc_label in {
        "SMC_GOOD_LOCATION_LONG",
        "SMC_GOOD_LOCATION_SHORT",
        "SMC_GOOD_LOCATION",
        "SMC_WATCHABLE_LOCATION",
        "SMC_MID_RANGE_WAIT_LONG",
        "SMC_MID_RANGE_WAIT_SHORT",
    }
    smc_wait = "WAIT_LOCATION" in smc_label or "MID_RANGE" in smc_label
    gate_fresh = freshness == "FRESH_GATE_TELEMETRY"
    entry_ready = latest_state == "ENTRY_READY_SHADOW" or gate_allow or final_allow

    lane = "DENY_HARD"
    lane_reason = "DEFAULT_DENY"

    if real_hard_reasons:
        lane = "DENY_HARD"
        lane_reason = "REAL_HARD_BLOCKER"
    elif action == "ALLOW_PAPER_ENTRY" and entry_ready and gate_fresh and score >= 75:
        lane = "ENTRY_READY"
        lane_reason = "F4X_ALLOW_WITH_ENTRY_READY_AND_FRESH_GATE"
    elif entry_ready and gate_fresh and cvdoi_good and trigger_good and smc_good and score >= 70:
        lane = "ENTRY_READY"
        lane_reason = "ENTRY_READY_BY_CONFLUENCE"
    elif gate_fresh and cvdoi_good and trigger_good and smc_good and score >= 55:
        lane = "EXECUTION_WATCH"
        lane_reason = "FRESH_EXECUTION_WATCH"
    elif action == "WATCH_ONLY" and cvdoi_good and trigger_good and score >= 55:
        lane = "DISCOVERY_WATCH"
        lane_reason = "FLOW_TRIGGER_WATCH_NEEDS_GATE_OR_SMC"
    elif action == "WATCH_ONLY" and p_watch >= 2 and score >= 50:
        lane = "DISCOVERY_WATCH"
        lane_reason = "PERSISTENT_WATCH_CANDIDATE"
    elif action == "RECHECK" or freshness in {"UNKNOWN", "NONE", ""} or latest_state in {"UNKNOWN", "NONE", ""}:
        lane = "RECHECK_DATA"
        lane_reason = "INSUFFICIENT_GATE_OR_LIFECYCLE_DATA"
    elif score >= 45 and cvd_status == "OK":
        lane = "RECHECK_DATA"
        lane_reason = "PARTIAL_CONFLUENCE_BUT_NOT_EXECUTION_GRADE"
    else:
        lane = "DENY_SOFT"
        lane_reason = "LOW_CONFLUENCE_NO_HARD_BLOCK"

    paper_bridge_allowed = lane == "ENTRY_READY"

    return {
        "pair": pair,
        "side": side,
        "source_action": action,
        "lane": lane,
        "lane_reason": lane_reason,
        "paper_bridge_allowed": paper_bridge_allowed,
        "score": score,
        "grade": grade,
        "cvdoi_label": cvdoi_label,
        "cvdoi_alignment": cvdoi_alignment,
        "cvd_status": cvd_status,
        "cvd_label": cvd_label,
        "trigger_status": trigger_status,
        "smc_clean_label": smc_label,
        "btc_guard": btc_guard,
        "latest_state": latest_state,
        "guarded_watch_status": guarded,
        "freshness_state": freshness,
        "gate_allow": gate_allow,
        "final_allow": final_allow,
        "direction_opposite": direction_opposite,
        "watch_count": p_watch,
        "recheck_count": p_recheck,
        "deny_count": p_deny,
        "persistence_score": persistence_score,
        "supports": supports,
        "blockers": blockers,
        "entry_blockers": entry_blockers,
        "real_hard_reasons": real_hard_reasons,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runtime-dir", default="user_data/revo_alpha/runtime/bybit")
    args = ap.parse_args()

    runtime = Path(args.runtime_dir)

    signals_path = runtime / "F4X_PAPER_DECISION_SIGNALS.json"
    aggregate_path = runtime / "F4X_MULTI_CYCLE_AGGREGATE_FULL.json"

    signals_state = load_json(signals_path, {})
    aggregate_state = load_json(aggregate_path, {})

    signals = signals_state.get("signals", []) if isinstance(signals_state, dict) else []
    if not isinstance(signals, list):
        signals = []

    persistence_map = build_persistence_map(aggregate_state if isinstance(aggregate_state, dict) else {})

    rows = []
    for s in signals:
        k = signal_key(s).split("|", 1)[0]
        p = persistence_map.get(k, {})
        rows.append(classify_lane(s, p))

    lane_counts = Counter(x["lane"] for x in rows)
    reason_counts = Counter(x["lane_reason"] for x in rows)
    smc_counts = Counter(x["smc_clean_label"] for x in rows)
    cvdoi_counts = Counter(x["cvdoi_label"] for x in rows)
    trigger_counts = Counter(x["trigger_status"] for x in rows)
    hard_counts = Counter(r for x in rows for r in x["real_hard_reasons"])

    entry_ready = [x for x in rows if x["lane"] == "ENTRY_READY"]
    execution_watch = [x for x in rows if x["lane"] == "EXECUTION_WATCH"]
    discovery_watch = [x for x in rows if x["lane"] == "DISCOVERY_WATCH"]
    recheck_data = [x for x in rows if x["lane"] == "RECHECK_DATA"]
    deny_hard = [x for x in rows if x["lane"] == "DENY_HARD"]
    deny_soft = [x for x in rows if x["lane"] == "DENY_SOFT"]

    persistent_watch = sorted(
        [x for x in rows if x["watch_count"] > 0],
        key=lambda x: (x["watch_count"], x["persistence_score"], x["score"]),
        reverse=True,
    )

    if entry_ready:
        final_decision = "F4X_C_ENTRY_READY_REVIEW_BEFORE_BRIDGE"
    elif execution_watch:
        final_decision = "F4X_C_EXECUTION_WATCH_ACTIVE_NO_ENTRY"
    elif discovery_watch:
        final_decision = "F4X_C_DISCOVERY_WATCH_ACTIVE_NO_ENTRY"
    elif recheck_data:
        final_decision = "F4X_C_RECHECK_DATA_REQUIRED"
    else:
        final_decision = "F4X_C_NO_ACTIONABLE_WATCH"

    payload = {
        "event": "F4X_C_LANE_SEPARATION_SMC_WATCH_PERSISTENCE",
        "generated_at": utc_now(),
        "runtime_dir": str(runtime),
        "source_signals": str(signals_path),
        "source_aggregate": str(aggregate_path),
        "final_decision": final_decision,
        "candidate_count": len(rows),
        "lane_counts": lane_counts.most_common(),
        "reason_counts": reason_counts.most_common(),
        "smc_clean_counts": smc_counts.most_common(),
        "cvdoi_counts": cvdoi_counts.most_common(),
        "trigger_counts": trigger_counts.most_common(),
        "hard_reason_counts": hard_counts.most_common(),
        "entry_ready": entry_ready,
        "execution_watch": execution_watch,
        "discovery_watch": discovery_watch,
        "recheck_data": recheck_data,
        "deny_hard": deny_hard,
        "deny_soft": deny_soft,
        "persistent_watch": persistent_watch,
        "lanes": rows,
        "paper_bridge_allowed": bool(entry_ready),
        "live_allowed": False,
        "risk_change": "NONE",
        "gate_loosen": "NONE",
    }

    out_json = runtime / "F4X_C_LANE_SEPARATION_FULL.json"
    out_compact = runtime / "F4X_C_LANE_SEPARATION_COMPACT.txt"
    root_compact = Path("F4X_C_LANE_SEPARATION_COMPACT.txt")

    write_json(out_json, payload)

    lines = []
    lines.append("F4X_C_LANE_SEPARATION_COMPACT")
    lines.append(f"generated_at={payload['generated_at']}")
    lines.append(f"runtime_dir={runtime}")
    lines.append("mode=LANE_SEPARATION_SMC_CLEANUP_WATCH_PERSISTENCE")
    lines.append("paper_signal_only=True")
    lines.append("live_allowed=False")
    lines.append("risk_change=NONE")
    lines.append("gate_loosen=NONE")
    lines.append("")
    lines.append("FINAL_DECISION")
    lines.append(f"final_decision={final_decision}")
    lines.append("")
    lines.append("COUNTS")
    lines.append(f"candidate_count={len(rows)}")
    lines.append(f"entry_ready_count={len(entry_ready)}")
    lines.append(f"execution_watch_count={len(execution_watch)}")
    lines.append(f"discovery_watch_count={len(discovery_watch)}")
    lines.append(f"recheck_data_count={len(recheck_data)}")
    lines.append(f"deny_hard_count={len(deny_hard)}")
    lines.append(f"deny_soft_count={len(deny_soft)}")
    lines.append("")
    lines.append("LANE_COUNTS")
    for k, v in lane_counts.most_common():
        lines.append(f"{k}={v}")
    lines.append("")
    lines.append("SMC_CLEAN_COUNTS")
    for k, v in smc_counts.most_common():
        lines.append(f"{k}={v}")
    lines.append("")
    lines.append("TOP_HARD_REASONS")
    for k, v in hard_counts.most_common(30):
        lines.append(f"{k}={v}")
    lines.append("")
    lines.append("ENTRY_READY")
    for x in entry_ready:
        lines.append(
            f"{x['pair']}|side={x['side']}|score={x['score']}|grade={x['grade']}|reason={x['lane_reason']}|"
            f"cvdoi={x['cvdoi_label']}|trigger={x['trigger_status']}|smc={x['smc_clean_label']}|fresh={x['freshness_state']}"
        )
    lines.append("")
    lines.append("EXECUTION_WATCH")
    for x in execution_watch:
        lines.append(
            f"{x['pair']}|side={x['side']}|score={x['score']}|grade={x['grade']}|reason={x['lane_reason']}|"
            f"cvdoi={x['cvdoi_label']}|trigger={x['trigger_status']}|smc={x['smc_clean_label']}|fresh={x['freshness_state']}|watch_count={x['watch_count']}"
        )
    lines.append("")
    lines.append("DISCOVERY_WATCH")
    for x in discovery_watch[:60]:
        lines.append(
            f"{x['pair']}|side={x['side']}|score={x['score']}|grade={x['grade']}|reason={x['lane_reason']}|"
            f"cvdoi={x['cvdoi_label']}|trigger={x['trigger_status']}|smc={x['smc_clean_label']}|"
            f"latest={x['latest_state']}|fresh={x['freshness_state']}|watch_count={x['watch_count']}|persistence={x['persistence_score']}"
        )
    lines.append("")
    lines.append("PERSISTENT_WATCH_TOP")
    for x in persistent_watch[:30]:
        lines.append(
            f"{x['pair']}|side={x['side']}|lane={x['lane']}|watch_count={x['watch_count']}|"
            f"recheck_count={x['recheck_count']}|deny_count={x['deny_count']}|persistence={x['persistence_score']}|"
            f"score={x['score']}|cvdoi={x['cvdoi_label']}|smc={x['smc_clean_label']}"
        )
    lines.append("")
    lines.append("RECHECK_DATA_TOP")
    for x in recheck_data[:40]:
        lines.append(
            f"{x['pair']}|side={x['side']}|score={x['score']}|reason={x['lane_reason']}|"
            f"latest={x['latest_state']}|fresh={x['freshness_state']}|smc={x['smc_clean_label']}|cvdoi={x['cvdoi_label']}"
        )
    lines.append("")
    lines.append("DECISION")
    if entry_ready:
        lines.append("REVIEW_ENTRY_READY_BEFORE_ENABLING_PAPER_BRIDGE")
    elif execution_watch:
        lines.append("KEEP_SIGNAL_ONLY_LOOP_AND_WAIT_FOR_ENTRY_READY")
    elif discovery_watch:
        lines.append("DISCOVERY_WATCH_ACTIVE_BUT_NOT_EXECUTION_READY")
    else:
        lines.append("NO_ENTRY_NO_EXECUTION_WATCH")
    lines.append("PAPER_STRATEGY_BRIDGE=HOLD_UNTIL_ENTRY_READY")
    lines.append("LIVE=HOLD")
    lines.append("RISK_UP=HOLD")
    lines.append("GATE_LOOSEN=HOLD")
    lines.append("")
    lines.append("OUTPUT_FILES")
    lines.append(f"full_json={out_json}")
    lines.append(f"compact={out_compact}")

    text = "\n".join(lines) + "\n"
    out_compact.write_text(text, encoding="utf-8")
    root_compact.write_text(text, encoding="utf-8")

    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
