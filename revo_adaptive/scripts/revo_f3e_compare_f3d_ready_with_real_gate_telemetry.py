#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from datetime import datetime, timezone
from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional


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
    rows = []
    for line in lines[-max_lines:]:
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except Exception:
            continue
    return rows


def side_fields(side: str) -> Dict[str, str]:
    side = side.upper()
    if side == "LONG":
        return {
            "final_allow": "final_allow_long",
            "gate_allow": "gate_allow_long",
            "score_allow": "score_would_allow_long",
            "final_reason": "final_reason_long",
            "gate_reason": "gate_reason_long",
            "grade": "shadow_trade_grade_long",
            "score": "shadow_confluence_score_long",
            "mandatory": "shadow_mandatory_pass_long",
        }
    if side == "SHORT":
        return {
            "final_allow": "final_allow_short",
            "gate_allow": "gate_allow_short",
            "score_allow": "score_would_allow_short",
            "final_reason": "final_reason_short",
            "gate_reason": "gate_reason_short",
            "grade": "shadow_trade_grade_short",
            "score": "shadow_confluence_score_short",
            "mandatory": "shadow_mandatory_pass_short",
        }
    return {}


def classify_reason(reason: str) -> str:
    r = reason.upper()
    if r in {"UNKNOWN", "NONE", ""}:
        return "NO_REASON"
    if "ALLOW" in r:
        return "ALLOW"
    if "TIMING" in r:
        return "TIMING_DENY"
    if "PREMIUM" in r or "DISCOUNT" in r or "RANGING_MID_RANGE" in r or "LOCATION" in r:
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


def event_ts(row: Dict[str, Any]) -> Optional[datetime]:
    return parse_dt(row.get("ts") or row.get("candle"))


def load_f3d_ready(runtime: Path) -> List[Dict[str, Any]]:
    state = load_json(runtime / "revo_f3d_current_flow_snapshot_scorer_state.json", {})
    rows = state.get("flow_ready", []) if isinstance(state, dict) else []

    # Deduplicate by pair + side, keep best score.
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


def latest_pair_events(events: List[Dict[str, Any]], pair: str, limit: int) -> List[Dict[str, Any]]:
    rows = [e for e in events if norm(e.get("pair")) == pair]
    rows.sort(key=lambda x: event_ts(x) or datetime.min.replace(tzinfo=timezone.utc))
    return rows[-limit:]


def analyze_candidate(candidate: Dict[str, Any], events: List[Dict[str, Any]], per_pair_limit: int) -> Dict[str, Any]:
    pair = norm(candidate.get("pair"))
    side = norm(candidate.get("side")).upper()
    fields = side_fields(side)

    pair_events = latest_pair_events(events, pair, per_pair_limit)

    final_allow_sum = 0
    gate_allow_sum = 0
    score_allow_sum = 0
    mandatory_sum = 0

    final_reasons = Counter()
    gate_reasons = Counter()
    reason_classes = Counter()
    score_vs_gate = Counter()
    grades = Counter()
    regimes = Counter()
    zones = Counter()
    directions = Counter()
    flow_dirs = Counter()
    source_events = Counter()

    latest_event = pair_events[-1] if pair_events else {}
    latest_ts = norm(latest_event.get("ts"))

    for e in pair_events:
        final_allow = as_int(e.get(fields.get("final_allow", "")))
        gate_allow = as_int(e.get(fields.get("gate_allow", "")))
        score_allow = as_int(e.get(fields.get("score_allow", "")))
        mandatory = as_int(e.get(fields.get("mandatory", "")))

        final_allow_sum += final_allow
        gate_allow_sum += gate_allow
        score_allow_sum += score_allow
        mandatory_sum += mandatory

        fr = norm(e.get(fields.get("final_reason", "")))
        gr = norm(e.get(fields.get("gate_reason", "")))
        final_reasons[fr] += 1
        gate_reasons[gr] += 1
        reason_classes[classify_reason(fr)] += 1

        score_vs_gate[norm(e.get("score_vs_gate"))] += 1
        grades[norm(e.get(fields.get("grade", "")))] += 1
        regimes[norm(e.get("regime_router"))] += 1
        zones[norm(e.get("pd_zone"))] += 1
        directions[norm(e.get("direction_engine"))] += 1
        flow_dirs[norm(e.get("flow_direction"))] += 1
        source_events[norm(e.get("event"))] += 1

    if not pair_events:
        gate_alignment = "NO_GATE_TELEMETRY_FOR_F3D_READY"
    elif final_allow_sum > 0:
        gate_alignment = "F3D_READY_GATE_FINAL_ALLOW_SEEN"
    elif score_allow_sum > 0 and gate_allow_sum == 0:
        gate_alignment = "F3D_READY_SCORE_ALLOW_BUT_GATE_DENY"
    elif reason_classes.get("TIMING_DENY", 0) > 0 and len(reason_classes) <= 2:
        gate_alignment = "F3D_READY_GATE_DENY_TIMING_DOMINANT"
    elif reason_classes.get("LOCATION_DENY", 0) > 0:
        gate_alignment = "F3D_READY_GATE_DENY_LOCATION_DOMINANT"
    elif reason_classes.get("TRAP_DENY", 0) > 0:
        gate_alignment = "F3D_READY_GATE_DENY_TRAP_DOMINANT"
    elif reason_classes.get("FLOW_DIRECTION_DENY", 0) > 0:
        gate_alignment = "F3D_READY_GATE_DENY_FLOW_DIRECTION_DOMINANT"
    elif reason_classes.get("CONTEXT_DENY", 0) > 0:
        gate_alignment = "F3D_READY_GATE_DENY_CONTEXT_DOMINANT"
    elif reason_classes.get("GEOMETRY_DENY", 0) > 0:
        gate_alignment = "F3D_READY_GATE_DENY_GEOMETRY_DOMINANT"
    else:
        gate_alignment = "F3D_READY_GATE_DENY_OTHER_OR_MIXED"

    recommendation = "AUDIT_ONLY_HOLD"
    if gate_alignment == "F3D_READY_GATE_FINAL_ALLOW_SEEN":
        recommendation = "OBSERVE_REAL_ENTRY_OUTCOME_ONLY"
    elif gate_alignment == "F3D_READY_SCORE_ALLOW_BUT_GATE_DENY":
        recommendation = "INVESTIGATE_GATE_BLOCK_REASON_BEFORE_PATCH"
    elif "TIMING" in gate_alignment:
        recommendation = "KEEP_FLOW_READY_AS_WAIT_TRIGGER_NOT_ENTRY"
    elif "LOCATION" in gate_alignment:
        recommendation = "KEEP_FLOW_READY_AS_WAIT_LOCATION_NOT_ENTRY"
    elif any(x in gate_alignment for x in ["TRAP", "CONTEXT", "GEOMETRY", "FLOW_DIRECTION"]):
        recommendation = "DO_NOT_LOOSEN_GATE_HARD_BLOCK_VALID_OR_NEEDS_SPECIFIC_AUDIT"

    return {
        "pair": pair,
        "side": side,
        "f3d_ratio": as_float(candidate.get("ratio")),
        "f3d_score": candidate.get("score"),
        "f3d_max_score": candidate.get("max_score"),
        "f3d_target_kind": norm(candidate.get("target_kind")),
        "event_count": len(pair_events),
        "latest_ts": latest_ts,
        "final_allow_sum": final_allow_sum,
        "gate_allow_sum": gate_allow_sum,
        "score_allow_sum": score_allow_sum,
        "mandatory_sum": mandatory_sum,
        "final_reasons": final_reasons.most_common(),
        "gate_reasons": gate_reasons.most_common(),
        "reason_classes": reason_classes.most_common(),
        "score_vs_gate": score_vs_gate.most_common(),
        "grades": grades.most_common(),
        "regimes": regimes.most_common(),
        "zones": zones.most_common(),
        "directions": directions.most_common(),
        "flow_dirs": flow_dirs.most_common(),
        "source_events": source_events.most_common(),
        "gate_alignment": gate_alignment,
        "recommendation": recommendation,
        "latest_event_summary": {
            "event": latest_event.get("event"),
            "ts": latest_event.get("ts"),
            "candle": latest_event.get("candle"),
            "score_vs_gate": latest_event.get("score_vs_gate"),
            "final_allow": latest_event.get(fields.get("final_allow", "")),
            "gate_allow": latest_event.get(fields.get("gate_allow", "")),
            "score_allow": latest_event.get(fields.get("score_allow", "")),
            "final_reason": latest_event.get(fields.get("final_reason", "")),
            "gate_reason": latest_event.get(fields.get("gate_reason", "")),
            "regime_router": latest_event.get("regime_router"),
            "pd_zone": latest_event.get("pd_zone"),
            "direction_engine": latest_event.get("direction_engine"),
            "flow_direction": latest_event.get("flow_direction"),
            "shadow_grade": latest_event.get(fields.get("grade", "")),
            "shadow_score": latest_event.get(fields.get("score", "")),
        },
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runtime-dir", default="user_data/revo_alpha/runtime/bybit")
    ap.add_argument("--jsonl-tail-lines", type=int, default=20000)
    ap.add_argument("--per-pair-limit", type=int, default=80)
    args = ap.parse_args()

    runtime = Path(args.runtime_dir)

    f3d_ready = load_f3d_ready(runtime)

    heartbeat = read_jsonl_tail(runtime / "revo_gate_heartbeat_events.jsonl", args.jsonl_tail_lines)
    shadow = read_jsonl_tail(runtime / "revo_gate_shadow_events.jsonl", args.jsonl_tail_lines)

    # Mark source for diagnostics.
    for e in heartbeat:
        e["_telemetry_file"] = "heartbeat"
    for e in shadow:
        e["_telemetry_file"] = "shadow"

    all_events = heartbeat + shadow

    reports = [analyze_candidate(c, all_events, args.per_pair_limit) for c in f3d_ready]

    alignment_counts = Counter(r["gate_alignment"] for r in reports)
    recommendation_counts = Counter(r["recommendation"] for r in reports)
    candidate_pairs = [f"{r['pair']}|{r['side']}" for r in reports]

    if not reports:
        decision = "F3E_NO_F3D_READY_CANDIDATES"
    elif any(r["gate_alignment"] == "F3D_READY_GATE_FINAL_ALLOW_SEEN" for r in reports):
        decision = "F3E_GATE_ALLOW_SEEN_OBSERVE_OUTCOME"
    elif any(r["gate_alignment"] == "F3D_READY_SCORE_ALLOW_BUT_GATE_DENY" for r in reports):
        decision = "F3E_GATE_DENY_AFTER_SCORE_ALLOW_INVESTIGATE_PRECISE_BLOCK"
    elif all("TIMING" in r["gate_alignment"] for r in reports if r["event_count"] > 0):
        decision = "F3E_FLOW_READY_WAIT_TRIGGER_CONFIRMED"
    else:
        decision = "F3E_GATE_STILL_DENIES_FLOW_READY_HOLD_BEHAVIOR_PATCH"

    payload = {
        "event": "F3E_COMPARE_F3D_READY_WITH_REAL_GATE_TELEMETRY",
        "generated_at": utc_now(),
        "runtime_dir": str(runtime),
        "jsonl_tail_lines": args.jsonl_tail_lines,
        "per_pair_limit": args.per_pair_limit,
        "heartbeat_events_loaded": len(heartbeat),
        "shadow_events_loaded": len(shadow),
        "f3d_ready_count": len(f3d_ready),
        "candidate_pairs": candidate_pairs,
        "alignment_counts": alignment_counts.most_common(),
        "recommendation_counts": recommendation_counts.most_common(),
        "reports": reports,
        "decision": decision,
        "behavior_change": "NONE",
        "entry_gate_change": "NONE",
        "risk_change": "NONE",
    }

    out_state = runtime / "revo_f3e_compare_f3d_ready_gate_telemetry_state.json"
    out_compact_runtime = runtime / "F3E_COMPARE_F3D_READY_GATE_TELEMETRY_COMPACT.txt"
    out_compact_root = Path("F3E_COMPARE_F3D_READY_GATE_TELEMETRY_COMPACT.txt")

    out_state.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    lines = []
    lines.append("F3E_COMPARE_F3D_READY_GATE_TELEMETRY_COMPACT")
    lines.append(f"generated_at={payload['generated_at']}")
    lines.append(f"runtime_dir={runtime}")
    lines.append("source=F3D_READY_VS_REAL_GATE_JSONL")
    lines.append("behavior_change=NONE")
    lines.append("entry_gate_change=NONE")
    lines.append("risk_change=NONE")
    lines.append("")
    lines.append("COUNTS")
    lines.append(f"heartbeat_events_loaded={len(heartbeat)}")
    lines.append(f"shadow_events_loaded={len(shadow)}")
    lines.append(f"f3d_ready_count={len(f3d_ready)}")
    lines.append(f"candidate_pairs={candidate_pairs}")
    lines.append("")
    lines.append("GATE_ALIGNMENT_COUNTS")
    for k, v in alignment_counts.most_common():
        lines.append(f"{k}={v}")
    lines.append("")
    lines.append("RECOMMENDATION_COUNTS")
    for k, v in recommendation_counts.most_common():
        lines.append(f"{k}={v}")
    lines.append("")
    lines.append("PAIR_GATE_COMPARISON")
    for r in reports:
        lines.append(
            f"{r['pair']}|side={r['side']}|f3d={r['f3d_score']}/{r['f3d_max_score']}|ratio={r['f3d_ratio']}|"
            f"events={r['event_count']}|latest_ts={r['latest_ts']}|"
            f"final_allow_sum={r['final_allow_sum']}|gate_allow_sum={r['gate_allow_sum']}|score_allow_sum={r['score_allow_sum']}|"
            f"alignment={r['gate_alignment']}|recommendation={r['recommendation']}"
        )
        lines.append(f"  final_reasons={r['final_reasons']}")
        lines.append(f"  gate_reasons={r['gate_reasons']}")
        lines.append(f"  reason_classes={r['reason_classes']}")
        lines.append(f"  score_vs_gate={r['score_vs_gate']}")
        lines.append(f"  regimes={r['regimes']}")
        lines.append(f"  zones={r['zones']}")
        lines.append(f"  directions={r['directions']}")
        lines.append(f"  flow_dirs={r['flow_dirs']}")
        lines.append(f"  latest_event={r['latest_event_summary']}")
    lines.append("")
    lines.append("DECISION")
    lines.append(decision)
    lines.append("NO_ENTRY_PROMOTION_FROM_THIS_REPORT_ALONE")
    lines.append("IF_GATE_DENIES_ONLY_TIMING_THEN_KEEP_WAIT_TRIGGER_MODEL")
    lines.append("IF_GATE_DENIES_LOCATION_GEOMETRY_TRAP_CONTEXT_THEN_DO_NOT_LOOSEN_GLOBAL_GATE")
    lines.append("NEXT_ACTION_DEPENDS_ON_TON_GATE_REASON")
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
