#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple


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


def read_jsonl(path: Path) -> List[Dict[str, Any]]:
    rows = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except Exception:
            continue
    return rows


def pair_key(row: Dict[str, Any]) -> str:
    return norm(row.get("pair") or row.get("symbol"))


def side_key(row: Dict[str, Any]) -> str:
    return norm(row.get("side")).upper()


def candidate_key(pair: str, side: str) -> str:
    return f"{pair}|{side.upper()}"


def get_event_time(row: Dict[str, Any]) -> Optional[datetime]:
    return parse_dt(row.get("candle") or row.get("ts") or row.get("generated_at") or row.get("candidate_ts"))


def timediff_sec(a: Optional[datetime], b: Optional[datetime]) -> float:
    if a is None or b is None:
        return 999999999.0
    return abs((a - b).total_seconds())


def load_candidates(runtime: Path) -> List[Dict[str, Any]]:
    f2x = load_json(runtime / "revo_f2x_shadow_outcome_state.json", {})
    f2y = load_json(runtime / "revo_f2y_trigger_failure_attribution_state.json", {})
    f2z_c = load_json(runtime / "revo_f2z_c_pair_bound_hardened_trigger_scorer_state.json", {})

    f2y_lookup = {}
    for row in f2y.get("classified", []) if isinstance(f2y, dict) else []:
        f2y_lookup[candidate_key(norm(row.get("pair")), norm(row.get("side")))] = row

    f2zc_lookup = {}
    for row in f2z_c.get("results", []) if isinstance(f2z_c, dict) else []:
        f2zc_lookup[candidate_key(norm(row.get("pair")), norm(row.get("side")))] = row

    out = []
    for row in f2x.get("results", []) if isinstance(f2x, dict) else []:
        pair = norm(row.get("pair"))
        side = norm(row.get("side")).upper()
        key = candidate_key(pair, side)
        merged = dict(row)
        merged["f2y"] = f2y_lookup.get(key, {})
        merged["f2z_c"] = f2zc_lookup.get(key, {})
        out.append(merged)
    return out


def find_best_event(
    events: List[Dict[str, Any]],
    pair: str,
    side: str,
    target_dt: Optional[datetime],
    max_abs_sec: int,
) -> Tuple[Optional[Dict[str, Any]], str, float]:
    best = None
    best_delta = 999999999.0

    for row in events:
        if pair_key(row) != pair:
            continue

        row_side = side_key(row)
        if row_side not in {"UNKNOWN", "NA", ""} and row_side != side:
            continue

        dt = get_event_time(row)
        delta = timediff_sec(dt, target_dt)

        if delta < best_delta:
            best = row
            best_delta = delta

    if best is None:
        return None, "NO_EVENT_FOR_PAIR", best_delta

    if best_delta > max_abs_sec:
        return best, "EVENT_FOUND_BUT_OUTSIDE_WINDOW", best_delta

    return best, "EVENT_ALIGNED", best_delta


def get_num(row: Dict[str, Any], names: List[str], default: float = 0.0) -> float:
    for name in names:
        for k, v in row.items():
            if name.lower() in str(k).lower():
                try:
                    return float(v)
                except Exception:
                    continue
    return default


def get_text(row: Dict[str, Any], names: List[str], default: str = "UNKNOWN") -> str:
    for name in names:
        for k, v in row.items():
            if name.lower() in str(k).lower():
                if v is not None and not isinstance(v, (dict, list)):
                    return str(v)
    return default


def outcome(row: Dict[str, Any], key: str) -> Dict[str, Any]:
    x = row.get(key)
    return x if isinstance(x, dict) else {}


def score_event(candidate: Dict[str, Any], event: Dict[str, Any], event_source: str, event_delta_sec: float) -> Dict[str, Any]:
    pair = norm(candidate.get("pair"))
    side = norm(candidate.get("side")).upper()

    f2y = candidate.get("f2y", {}) if isinstance(candidate.get("f2y"), dict) else {}
    f2zc = candidate.get("f2z_c", {}) if isinstance(candidate.get("f2z_c"), dict) else {}

    regime = norm(event.get("regime_router") or candidate.get("regime") or f2y.get("regime") or f2zc.get("regime")).upper()
    zone = norm(event.get("pd_zone") or candidate.get("pd_zone") or f2y.get("zone") or f2zc.get("zone")).upper()
    direction = norm(event.get("direction_engine") or f2y.get("direction") or f2zc.get("direction")).upper()

    flow_direction = norm(event.get("flow_direction") or event.get("flow_authority") or f2zc.get("flow_direction")).upper()
    flow_authority = norm(event.get("flow_authority") or event.get("authority") or f2zc.get("flow_authority")).upper()
    flow_risk = norm(event.get("flow_risk") or f2zc.get("flow_risk")).upper()
    flow_strength = norm(event.get("flow_strength") or event.get("sticky_current_strength") or f2zc.get("flow_strength")).upper()

    cvd_delta = get_num(event, ["cvd_delta_15m", "cvd_delta"])
    cvd_z = get_num(event, ["cvd_zscore_15m", "cvd_zscore", "cvd_z_15m", "cvd_z"])
    oi15 = get_num(event, ["oi_delta_pct_15m", "oi_delta_15m_pct", "open_interest_delta_15m_pct"])
    oi1h = get_num(event, ["oi_delta_pct_1h", "oi_delta_1h_pct", "open_interest_delta_1h_pct"])
    funding = get_num(event, ["funding_rate", "funding"], 0.0)

    o3 = outcome(candidate, "outcome_3c")
    o6 = outcome(candidate, "outcome_6c")
    mfe3 = as_float(o3.get("mfe_pct"))
    mae3 = as_float(o3.get("mae_pct"))
    close3 = as_float(o3.get("close_return_pct"))
    close6 = as_float(o6.get("close_return_pct"))

    score = 0
    max_score = 0
    tags: List[str] = []
    reasons: List[str] = []

    direction_ok = (side == "LONG" and direction == "LONG_ONLY") or (side == "SHORT" and direction == "SHORT_ONLY")
    location_ok = (side == "LONG" and zone == "DISCOUNT") or (side == "SHORT" and zone == "PREMIUM")
    flow_dir_ok = (side == "LONG" and flow_direction == "LONG_ONLY") or (side == "SHORT" and flow_direction == "SHORT_ONLY")

    max_score += 2
    if direction_ok:
        score += 2
        tags.append("DIRECTION_OK")
    else:
        tags.append("DIRECTION_FAIL")
        reasons.append("DIRECTION_FAIL")

    max_score += 2
    if location_ok:
        score += 2
        tags.append("LOCATION_OK")
    else:
        tags.append("LOCATION_FAIL")
        reasons.append("LOCATION_FAIL")

    max_score += 2
    if flow_dir_ok:
        score += 2
        tags.append("EVENT_FLOW_DIRECTION_OK")
    elif flow_direction in {"NO_TRADE", "UNKNOWN"}:
        tags.append("EVENT_FLOW_DIRECTION_NOT_READY")
    else:
        score += 1
        tags.append("EVENT_FLOW_DIRECTION_PARTIAL")

    max_score += 3
    if side == "LONG":
        if cvd_delta > 0 and cvd_z > 0:
            score += 3
            tags.append("EVENT_CVD_STRONG_LONG")
        elif cvd_delta > 0 or cvd_z > 0:
            score += 2
            tags.append("EVENT_CVD_PARTIAL_LONG")
        else:
            tags.append("EVENT_CVD_NOT_SUPPORT_LONG")
            reasons.append("CVD_NOT_SUPPORTIVE")
    else:
        if cvd_delta < 0 and cvd_z < 0:
            score += 3
            tags.append("EVENT_CVD_STRONG_SHORT")
        elif cvd_delta < 0 or cvd_z < 0:
            score += 2
            tags.append("EVENT_CVD_PARTIAL_SHORT")
        else:
            tags.append("EVENT_CVD_NOT_SUPPORT_SHORT")
            reasons.append("CVD_NOT_SUPPORTIVE")

    max_score += 3
    if oi15 > 0 and oi1h >= 0:
        score += 3
        tags.append("EVENT_OI_EXPANSION_ALIGNED")
    elif oi15 > 0:
        score += 2
        tags.append("EVENT_OI_EXPANSION_SHORT_TERM")
    else:
        tags.append("EVENT_OI_NOT_EXPANDING")
        reasons.append("OI_NOT_EXPANDING")

    max_score += 2
    if flow_authority == "ENTRY_ELIGIBLE":
        score += 2
        tags.append("EVENT_FLOW_AUTHORITY_ENTRY_ELIGIBLE")
    elif flow_authority == "WATCH_ONLY":
        score += 1
        tags.append("EVENT_FLOW_AUTHORITY_WATCH_ONLY")
    else:
        tags.append("EVENT_FLOW_AUTHORITY_NOT_READY")

    max_score += 2
    if "TRAP" in flow_risk:
        tags.append("EVENT_FLOW_TRAP_RISK")
        reasons.append("FLOW_TRAP_RISK")
    elif "STRONG" in flow_strength:
        score += 2
        tags.append("EVENT_FLOW_STRENGTH_STRONG")
    elif flow_strength not in {"NO_FLOW", "UNKNOWN"}:
        score += 1
        tags.append("EVENT_FLOW_STRENGTH_PARTIAL")
    else:
        tags.append("EVENT_FLOW_STRENGTH_WEAK_OR_UNKNOWN")

    max_score += 1
    if side == "LONG" and funding <= 0.02:
        score += 1
        tags.append("FUNDING_OK_LONG")
    elif side == "SHORT" and funding >= -0.02:
        score += 1
        tags.append("FUNDING_OK_SHORT")
    else:
        tags.append("FUNDING_NOT_IDEAL")

    tags.append("RSI_MISSING_EXPLICIT_NOT_SCORED")
    reasons.append("RSI_MISSING_EXPLICIT")

    if mae3 < 0 and abs(mae3) > max(mfe3, 0.0001):
        tags.append("OUTCOME_3C_MAE_DOMINATES_MFE")
    if close3 < 0:
        tags.append("OUTCOME_3C_CLOSE_NEGATIVE")
    if close6 < 0:
        tags.append("OUTCOME_6C_CLOSE_NEGATIVE")
    if mae3 <= -0.50:
        tags.append("OUTCOME_3C_MAE_LT_NEG_050")

    ratio = round(score / max_score, 4) if max_score else 0.0

    hard_fail = any(x in tags for x in [
        "DIRECTION_FAIL",
        "LOCATION_FAIL",
        "EVENT_FLOW_TRAP_RISK",
        "EVENT_CVD_NOT_SUPPORT_LONG",
        "EVENT_CVD_NOT_SUPPORT_SHORT",
        "EVENT_OI_NOT_EXPANDING",
    ])

    if event_source != "EVENT_ALIGNED":
        status = "EVENT_ALIGNMENT_WEAK"
    elif hard_fail:
        status = "EVENT_ALIGNED_FAIL_SIGNAL_CONFLICT"
    elif ratio >= 0.72 and close3 >= 0 and "OUTCOME_3C_MAE_DOMINATES_MFE" not in tags:
        status = "EVENT_ALIGNED_PASS_SHADOW_AUDIT"
    elif ratio >= 0.62 and close3 >= -0.10 and mae3 >= -0.50:
        status = "EVENT_ALIGNED_PARTIAL_SHADOW_AUDIT"
    else:
        status = "EVENT_ALIGNED_REJECT_SHADOW_AUDIT"

    return {
        "pair": pair,
        "side": side,
        "event_source": event_source,
        "event_delta_sec": round(event_delta_sec, 1),
        "status": status,
        "score": score,
        "max_score": max_score,
        "ratio": ratio,
        "regime": regime,
        "zone": zone,
        "direction": direction,
        "flow_direction": flow_direction,
        "flow_authority": flow_authority,
        "flow_risk": flow_risk,
        "flow_strength": flow_strength,
        "cvd_delta_15m": cvd_delta,
        "cvd_z": cvd_z,
        "oi_delta_15m": oi15,
        "oi_delta_1h": oi1h,
        "funding": funding,
        "mfe3": mfe3,
        "mae3": mae3,
        "close3": close3,
        "close6": close6,
        "tags": tags,
        "reasons": sorted(set(reasons)),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runtime-dir", default="user_data/revo_alpha/runtime/bybit")
    ap.add_argument("--max-align-sec", type=int, default=900)
    args = ap.parse_args()

    runtime = Path(args.runtime_dir)

    candidates = load_candidates(runtime)

    event_sources = [
        ("F2U_SETUP_EVENTS", read_jsonl(runtime / "revo_f2u_setup_state_events.jsonl")),
        ("GATE_SHADOW_EVENTS", read_jsonl(runtime / "revo_gate_shadow_events.jsonl")),
        ("GATE_HEARTBEAT_EVENTS", read_jsonl(runtime / "revo_gate_heartbeat_events.jsonl")),
    ]

    results = []
    status_counts = Counter()
    tag_counts = Counter()
    reason_counts = Counter()
    source_counts = Counter()

    for c in candidates:
        pair = norm(c.get("pair"))
        side = norm(c.get("side")).upper()
        target_dt = parse_dt(c.get("candidate_ts") or c.get("source_candle") or c.get("source_ts"))

        best_row = None
        best_source = "NO_EVENT"
        best_delta = 999999999.0
        best_status = "NO_EVENT"

        for source_name, rows in event_sources:
            row, status, delta = find_best_event(rows, pair, side, target_dt, args.max_align_sec)
            if row is not None and delta < best_delta:
                best_row = row
                best_source = source_name
                best_delta = delta
                best_status = status

        if best_row is None:
            best_row = {}
            best_status = "NO_EVENT_FOR_PAIR"

        scored = score_event(c, best_row, best_status, best_delta)
        scored["event_file_source"] = best_source
        results.append(scored)

        status_counts[scored["status"]] += 1
        source_counts[best_source] += 1
        for t in scored["tags"]:
            tag_counts[t] += 1
        for r in scored["reasons"]:
            reason_counts[r] += 1

    if status_counts.get("EVENT_ALIGNED_PASS_SHADOW_AUDIT", 0) >= 2:
        decision = "EVENT_ALIGNED_SCORER_PROMISING_NEEDS_MORE_BATCH"
    elif status_counts.get("EVENT_ALIGNED_PASS_SHADOW_AUDIT", 0) == 1:
        decision = "EVENT_ALIGNED_SCORER_SELECTIVE_ONLY_MORE_BATCH_REQUIRED"
    elif status_counts.get("EVENT_ALIGNMENT_WEAK", 0) > 0:
        decision = "EVENT_ALIGNMENT_WEAK_FIX_TIMESTAMP_LINEAGE"
    else:
        decision = "EVENT_ALIGNED_SCORER_NOT_READY"

    payload = {
        "event": "F2Z_D_EVENT_ALIGNED_HARDENED_SCORER",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "runtime_dir": str(runtime),
        "candidate_count": len(results),
        "decision": decision,
        "status_counts": status_counts.most_common(),
        "source_counts": source_counts.most_common(),
        "tag_counts": tag_counts.most_common(),
        "reason_counts": reason_counts.most_common(),
        "results": results,
        "behavior_change": "NONE",
        "entry_gate_change": "NONE",
        "risk_change": "NONE",
    }

    out_state = runtime / "revo_f2z_d_event_aligned_hardened_scorer_state.json"
    out_compact_runtime = runtime / "F2Z_D_EVENT_ALIGNED_HARDENED_SCORER_COMPACT.txt"
    out_compact_root = Path("F2Z_D_EVENT_ALIGNED_HARDENED_SCORER_COMPACT.txt")

    out_state.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    lines = []
    lines.append("F2Z_D_EVENT_ALIGNED_HARDENED_SCORER_COMPACT")
    lines.append(f"generated_at={payload['generated_at']}")
    lines.append(f"runtime_dir={runtime}")
    lines.append(f"candidate_count={len(results)}")
    lines.append(f"decision={decision}")
    lines.append("behavior_change=NONE")
    lines.append("entry_gate_change=NONE")
    lines.append("risk_change=NONE")
    lines.append("")

    lines.append("STATUS_COUNTS")
    for k, v in status_counts.most_common():
        lines.append(f"{k}={v}")
    lines.append("")

    lines.append("SOURCE_COUNTS")
    for k, v in source_counts.most_common():
        lines.append(f"{k}={v}")
    lines.append("")

    lines.append("TOP_TAGS")
    for k, v in tag_counts.most_common(40):
        lines.append(f"{k}={v}")
    lines.append("")

    lines.append("REASONS")
    for k, v in reason_counts.most_common():
        lines.append(f"{k}={v}")
    lines.append("")

    lines.append("PAIR_EVENT_ALIGNED_SCORES")
    for r in results:
        lines.append(
            "|".join([
                str(r["status"]),
                str(r["pair"]),
                str(r["side"]),
                f"event_file={r['event_file_source']}",
                f"event_source={r['event_source']}",
                f"event_delta_sec={r['event_delta_sec']}",
                f"score={r['score']}/{r['max_score']}",
                f"ratio={r['ratio']}",
                f"regime={r['regime']}",
                f"zone={r['zone']}",
                f"direction={r['direction']}",
                f"flow_direction={r['flow_direction']}",
                f"flow_authority={r['flow_authority']}",
                f"flow_risk={r['flow_risk']}",
                f"flow_strength={r['flow_strength']}",
                f"cvd_delta_15m={r['cvd_delta_15m']}",
                f"cvd_z={r['cvd_z']}",
                f"oi_delta_15m={r['oi_delta_15m']}",
                f"oi_delta_1h={r['oi_delta_1h']}",
                f"mfe3={r['mfe3']}",
                f"mae3={r['mae3']}",
                f"close3={r['close3']}",
                f"close6={r['close6']}",
                f"tags={','.join(r['tags'])}",
                f"reasons={','.join(r['reasons'])}",
            ])
        )
    lines.append("")

    lines.append("DECISION")
    lines.append("NO_ENTRY_GATE_RISK_CHANGE")
    lines.append("IF_EVENT_ALIGNED_STILL_REJECTS_ALL_GATE_IS_VALID_AND_TRIGGER_PROMOTION_REJECTED")
    lines.append("IF_EVENT_ALIGNMENT_WEAK_FIX_TIMESTAMP_LINEAGE_BEFORE_SCORER")
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
