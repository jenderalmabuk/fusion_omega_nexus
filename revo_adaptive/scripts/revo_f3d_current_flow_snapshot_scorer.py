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
        if v is None:
            return default
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


def score_snapshot(x: Dict[str, Any]) -> Dict[str, Any]:
    pair = norm(x.get("pair"))
    target_kind = norm(x.get("target_kind"))
    action = norm(x.get("watch_action"))
    status = norm(x.get("snapshot_status"))
    bias = norm(x.get("primary_bias"))
    trend = norm(x.get("trending_interpretation"))
    trap = norm(x.get("trap_interpretation"))
    in_pairlist = int(x.get("in_current_pairlist") or 0)

    oi1h = as_float(x.get("oi_1h_delta_pct"))
    oi15 = as_float(x.get("oi_15m_delta_pct"))
    oi5 = x.get("oi_5m_delta_pct")
    oi5v = None if oi5 is None else as_float(oi5)

    px15 = as_float(x.get("price_15m_delta_pct"))
    px5 = x.get("price_5m_delta_pct")
    px5v = None if px5 is None else as_float(px5)

    reasons = []
    score = 0
    max_score = 10

    if status == "SNAPSHOT_READY":
        score += 1
        reasons.append("SNAPSHOT_READY")
    else:
        reasons.append("SNAPSHOT_NOT_READY")

    if in_pairlist:
        score += 1
        reasons.append("IN_CURRENT_PAIRLIST")
    else:
        reasons.append("NOT_IN_CURRENT_PAIRLIST")

    if bias == "LONG_FLOW":
        score += 2
        side = "LONG"
        reasons.append("LONG_FLOW")
    elif bias == "SHORT_FLOW":
        score += 2
        side = "SHORT"
        reasons.append("SHORT_FLOW")
    elif bias == "WEAK_LONG_FLOW":
        score += 1
        side = "LONG"
        reasons.append("WEAK_LONG_FLOW")
    elif bias == "WEAK_SHORT_FLOW":
        score += 1
        side = "SHORT"
        reasons.append("WEAK_SHORT_FLOW")
    elif bias == "TRAP_WARNING":
        side = "NONE"
        reasons.append("TRAP_WARNING")
    else:
        side = "NONE"
        reasons.append("NO_CLEAR_BIAS")

    if trend in {"TRENDING_LONG_FLOW_OK", "TRENDING_SHORT_FLOW_OK"}:
        score += 2
        reasons.append("TRENDING_FLOW_OK")
    elif "TRAP" in trend:
        reasons.append("TRENDING_TRAP_OR_CONFLICT")
    else:
        reasons.append("TRENDING_NOT_CONFIRMED")

    if trap == "TRAP_RISK_LOW":
        score += 2
        reasons.append("TRAP_LOW")
    elif trap == "TRAP_RISK_WATCH":
        score += 1
        reasons.append("TRAP_WATCH")
    else:
        reasons.append("TRAP_ELEVATED")

    # OI/price coherence.
    if side == "LONG":
        if oi1h > 0 and oi15 > 0 and px15 > 0:
            score += 1
            reasons.append("CORE_OI_PRICE_LONG_OK")
        else:
            reasons.append("CORE_OI_PRICE_LONG_WEAK")
        if oi5v is not None and px5v is not None and oi5v > 0 and px5v > 0:
            score += 1
            reasons.append("FAST_5M_LONG_OK")
        elif oi5v is None:
            reasons.append("FAST_5M_NOT_AVAILABLE")
        else:
            reasons.append("FAST_5M_LONG_NOT_CONFIRMED")

    elif side == "SHORT":
        if oi1h > 0 and oi15 > 0 and px15 < 0:
            score += 1
            reasons.append("CORE_OI_PRICE_SHORT_OK")
        else:
            reasons.append("CORE_OI_PRICE_SHORT_WEAK")
        if oi5v is not None and px5v is not None and oi5v > 0 and px5v < 0:
            score += 1
            reasons.append("FAST_5M_SHORT_OK")
        elif oi5v is None:
            reasons.append("FAST_5M_NOT_AVAILABLE")
        else:
            reasons.append("FAST_5M_SHORT_NOT_CONFIRMED")

    else:
        reasons.append("NO_SIDE_TO_SCORE")

    ratio = round(score / max_score, 4)

    # Audit-only status.
    if status != "SNAPSHOT_READY":
        decision = "REJECT_SNAPSHOT_NOT_READY"
    elif bias == "TRAP_WARNING" or trap == "TRAP_RISK_ELEVATED":
        decision = "AVOID_TRAP"
    elif action == "OLD_CANDIDATE_ALIGNMENT_AUDIT":
        decision = "OLD_REPLAY_ONLY_NO_ACTION"
    elif ratio >= 0.80 and side in {"LONG", "SHORT"}:
        decision = "FLOW_READY_WAIT_TRIGGER"
    elif ratio >= 0.60 and side in {"LONG", "SHORT"}:
        decision = "WATCH_FLOW_WAIT_CONFIRMATION"
    elif side in {"LONG", "SHORT"}:
        decision = "WEAK_FLOW_OBSERVE_ONLY"
    else:
        decision = "NO_TRADE_FLOW"

    return {
        "pair": pair,
        "side": side,
        "target_kind": target_kind,
        "in_current_pairlist": in_pairlist,
        "snapshot_status": status,
        "input_action": action,
        "primary_bias": bias,
        "trend": trend,
        "trap": trap,
        "score": score,
        "max_score": max_score,
        "ratio": ratio,
        "decision": decision,
        "oi_1h_delta_pct": oi1h,
        "oi_15m_delta_pct": oi15,
        "oi_5m_delta_pct": oi5v,
        "price_15m_delta_pct": px15,
        "price_5m_delta_pct": px5v,
        "reasons": reasons,
        "behavior_change": "NONE",
        "entry_gate_change": "NONE",
        "risk_change": "NONE",
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runtime-dir", default="user_data/revo_alpha/runtime/bybit")
    args = ap.parse_args()

    runtime = Path(args.runtime_dir)

    f3c = load_json(runtime / "revo_f3c_event_aligned_flow_snapshot_state.json", {})
    snapshots = f3c.get("snapshots", []) if isinstance(f3c, dict) else []

    scored = [score_snapshot(x) for x in snapshots]

    current = [x for x in scored if x["target_kind"] == "CURRENT_PAIRLIST"]
    flow_ready = [x for x in scored if x["decision"] == "FLOW_READY_WAIT_TRIGGER"]
    watch_confirm = [x for x in scored if x["decision"] == "WATCH_FLOW_WAIT_CONFIRMATION"]
    avoid_trap = [x for x in scored if x["decision"] == "AVOID_TRAP"]
    weak = [x for x in scored if x["decision"] == "WEAK_FLOW_OBSERVE_ONLY"]

    decision_counts = Counter(x["decision"] for x in scored)
    current_decision_counts = Counter(x["decision"] for x in current)
    bias_counts = Counter(x["primary_bias"] for x in scored)
    side_counts = Counter(x["side"] for x in scored)
    reason_counts = Counter(r for x in scored for r in x["reasons"])

    if len(flow_ready) > 0:
        final_decision = "F3D_FLOW_READY_EXISTS_AUDIT_ONLY"
    elif len(watch_confirm) > 0:
        final_decision = "F3D_WATCH_CONFIRMATION_ONLY"
    else:
        final_decision = "F3D_NO_FLOW_READY"

    payload = {
        "event": "F3D_CURRENT_FLOW_SNAPSHOT_SCORER",
        "generated_at": utc_now(),
        "runtime_dir": str(runtime),
        "snapshot_count": len(scored),
        "current_pairlist_count": len(current),
        "decision": final_decision,
        "decision_counts": decision_counts.most_common(),
        "current_decision_counts": current_decision_counts.most_common(),
        "bias_counts": bias_counts.most_common(),
        "side_counts": side_counts.most_common(),
        "reason_counts": reason_counts.most_common(),
        "flow_ready": flow_ready,
        "watch_confirm": watch_confirm,
        "avoid_trap": avoid_trap,
        "weak": weak,
        "current_pairlist_scored": current,
        "scored": scored,
        "behavior_change": "NONE",
        "entry_gate_change": "NONE",
        "risk_change": "NONE",
    }

    out_state = runtime / "revo_f3d_current_flow_snapshot_scorer_state.json"
    out_compact_runtime = runtime / "F3D_CURRENT_FLOW_SNAPSHOT_SCORER_COMPACT.txt"
    out_compact_root = Path("F3D_CURRENT_FLOW_SNAPSHOT_SCORER_COMPACT.txt")

    out_state.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    lines = []
    lines.append("F3D_CURRENT_FLOW_SNAPSHOT_SCORER_COMPACT")
    lines.append(f"generated_at={payload['generated_at']}")
    lines.append(f"runtime_dir={runtime}")
    lines.append("storage=F3C_SQLITE_SNAPSHOT_READ_ONLY_AUDIT")
    lines.append("behavior_change=NONE")
    lines.append("entry_gate_change=NONE")
    lines.append("risk_change=NONE")
    lines.append("")
    lines.append("COUNTS")
    lines.append(f"snapshot_count={len(scored)}")
    lines.append(f"current_pairlist_count={len(current)}")
    lines.append(f"flow_ready_count={len(flow_ready)}")
    lines.append(f"watch_confirmation_count={len(watch_confirm)}")
    lines.append(f"avoid_trap_count={len(avoid_trap)}")
    lines.append(f"weak_flow_count={len(weak)}")
    lines.append("")
    lines.append("DECISION_COUNTS")
    for k, v in decision_counts.most_common():
        lines.append(f"{k}={v}")
    lines.append("")
    lines.append("CURRENT_PAIRLIST_DECISION_COUNTS")
    for k, v in current_decision_counts.most_common():
        lines.append(f"{k}={v}")
    lines.append("")
    lines.append("BIAS_COUNTS")
    for k, v in bias_counts.most_common():
        lines.append(f"{k}={v}")
    lines.append("")
    lines.append("TOP_REASONS")
    for k, v in reason_counts.most_common(40):
        lines.append(f"{k}={v}")
    lines.append("")
    lines.append("CURRENT_PAIRLIST_SCORED")
    for x in current:
        lines.append(
            f"{x['pair']}|decision={x['decision']}|side={x['side']}|score={x['score']}/{x['max_score']}|"
            f"ratio={x['ratio']}|bias={x['primary_bias']}|trend={x['trend']}|trap={x['trap']}|"
            f"oi1h={x['oi_1h_delta_pct']}|oi15={x['oi_15m_delta_pct']}|oi5={x['oi_5m_delta_pct']}|"
            f"px15={x['price_15m_delta_pct']}|px5={x['price_5m_delta_pct']}|reasons={','.join(x['reasons'])}"
        )
    lines.append("")
    lines.append("FLOW_READY_WAIT_TRIGGER_AUDIT")
    for x in flow_ready[:30]:
        lines.append(
            f"{x['pair']}|target={x['target_kind']}|side={x['side']}|score={x['score']}/{x['max_score']}|"
            f"ratio={x['ratio']}|bias={x['primary_bias']}|trend={x['trend']}|trap={x['trap']}|"
            f"oi15={x['oi_15m_delta_pct']}|oi5={x['oi_5m_delta_pct']}|px15={x['price_15m_delta_pct']}|px5={x['price_5m_delta_pct']}"
        )
    lines.append("")
    lines.append("WATCH_CONFIRMATION_AUDIT")
    for x in watch_confirm[:40]:
        lines.append(
            f"{x['pair']}|target={x['target_kind']}|side={x['side']}|score={x['score']}/{x['max_score']}|"
            f"ratio={x['ratio']}|bias={x['primary_bias']}|trend={x['trend']}|trap={x['trap']}|reasons={','.join(x['reasons'])}"
        )
    lines.append("")
    lines.append("AVOID_TRAP_AUDIT")
    for x in avoid_trap[:40]:
        lines.append(
            f"{x['pair']}|target={x['target_kind']}|score={x['score']}/{x['max_score']}|"
            f"bias={x['primary_bias']}|trend={x['trend']}|trap={x['trap']}|reasons={','.join(x['reasons'])}"
        )
    lines.append("")
    lines.append("DECISION")
    lines.append(final_decision)
    lines.append("DO_NOT_CONNECT_TO_ENTRY_GATE_YET")
    lines.append("NEXT_F3E_CAN_COMPARE_F3D_READY_WITH_REAL_GATE_TELEMETRY_AUDIT_ONLY")
    lines.append("NO_ENTRY_PROMOTION_FROM_THIS_REPORT_ALONE")
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
