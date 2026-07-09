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


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return default


def pair_tokens(pair: str) -> List[str]:
    p = norm(pair)
    base = p.split("/")[0] if "/" in p else p.replace("USDT", "")
    return [p, f"{base}/USDT:USDT", f"{base}/USDT", f"{base}USDT", base]


def row_has_pair_identity(row: Any, pair: str) -> bool:
    if not isinstance(row, dict):
        return False
    tokens = set(pair_tokens(pair))
    for k in ["pair", "symbol", "market", "instrument", "base", "base_currency", "pair_name", "pair_symbol"]:
        if norm(row.get(k)) in tokens:
            return True
    return False


def get_pair_obj(data: Any, pair: str) -> Optional[Dict[str, Any]]:
    if not isinstance(data, dict):
        return None

    for token in pair_tokens(pair):
        v = data.get(token)
        if isinstance(v, dict):
            return v

    pairs = data.get("pairs")
    if isinstance(pairs, dict):
        for token in pair_tokens(pair):
            v = pairs.get(token)
            if isinstance(v, dict):
                return v

    if isinstance(pairs, list):
        for row in pairs:
            if row_has_pair_identity(row, pair):
                return row

    return None


def deep_get_numbers(obj: Any, wanted: List[str]) -> Dict[str, float]:
    out: Dict[str, float] = {}

    def walk(x: Any, prefix: str = "") -> None:
        if isinstance(x, dict):
            for k, v in x.items():
                key = f"{prefix}.{k}" if prefix else str(k)
                lk = key.lower()
                if any(w in lk for w in wanted):
                    try:
                        out[key] = float(v)
                    except Exception:
                        pass
                if isinstance(v, (dict, list)):
                    walk(v, key)
        elif isinstance(x, list):
            for i, v in enumerate(x[:20]):
                walk(v, f"{prefix}[{i}]")

    walk(obj)
    return out


def deep_get_text(obj: Any, wanted: List[str]) -> Dict[str, str]:
    out: Dict[str, str] = {}

    def walk(x: Any, prefix: str = "") -> None:
        if isinstance(x, dict):
            for k, v in x.items():
                key = f"{prefix}.{k}" if prefix else str(k)
                lk = key.lower()
                if any(w in lk for w in wanted):
                    if v is not None and not isinstance(v, (dict, list)):
                        out[key] = str(v)
                if isinstance(v, (dict, list)):
                    walk(v, key)
        elif isinstance(x, list):
            for i, v in enumerate(x[:20]):
                walk(v, f"{prefix}[{i}]")

    walk(obj)
    return out


def first_num(metrics: Dict[str, float], names: List[str], default: float = 0.0) -> float:
    for name in names:
        for k, v in metrics.items():
            if name.lower() in k.lower():
                return v
    return default


def first_text(texts: Dict[str, str], names: List[str], default: str = "UNKNOWN") -> str:
    for name in names:
        for k, v in texts.items():
            if name.lower() in k.lower():
                return str(v)
    return default


def keyed(rows: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    out = {}
    for r in rows:
        pair = norm(r.get("pair"))
        side = norm(r.get("side")).upper()
        if pair != "UNKNOWN" and side != "UNKNOWN":
            out[f"{pair}|{side}"] = r
    return out


def outcome_keyed(f2x: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return keyed(f2x.get("results", []) if isinstance(f2x, dict) else [])


def f2w_keyed(f2w: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return keyed(f2w.get("rows", []) if isinstance(f2w, dict) else [])


def f2y_keyed(f2y: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return keyed(f2y.get("classified", []) if isinstance(f2y, dict) else [])


def score_pair(
    candidate: Dict[str, Any],
    flow_obj: Dict[str, Any],
    exec_obj: Dict[str, Any],
    collector_obj: Dict[str, Any],
    f2w_row: Dict[str, Any],
    f2x_row: Dict[str, Any],
    f2y_row: Dict[str, Any],
) -> Dict[str, Any]:
    pair = norm(candidate.get("pair"))
    side = norm(candidate.get("side")).upper()
    regime = norm(candidate.get("regime")).upper()
    zone = norm(candidate.get("zone")).upper()
    direction = norm(candidate.get("direction")).upper()

    # Prefer execution context, then flow context, then collector.
    merged: Dict[str, Any] = {}
    for obj in [collector_obj, flow_obj, exec_obj]:
        if isinstance(obj, dict):
            merged.update(obj)

    nums = deep_get_numbers(merged, [
        "cvd", "oi", "open_interest", "funding", "volume", "price_delta",
        "pd_location", "btc_weight",
    ])
    texts = deep_get_text(merged, [
        "cvd", "oi", "structure", "flow", "direction", "authority",
        "quadrant", "strength", "risk", "quality", "source",
    ])

    cvd_delta_15m = first_num(nums, ["cvd_delta_15m", "cvd_delta"])
    cvd_z = first_num(nums, ["cvd_zscore_15m", "cvd_zscore", "cvd_z_15m", "cvd_z"])
    oi_delta_15m = first_num(nums, ["oi_delta_pct_15m", "oi_delta_15m_pct", "open_interest_delta_15m_pct"])
    oi_delta_1h = first_num(nums, ["oi_delta_pct_1h", "oi_delta_1h_pct", "open_interest_delta_1h_pct"])
    funding = first_num(nums, ["funding_rate", "funding"], 0.0)

    cvd_structure = first_text(texts, ["cvd_structure"], "UNKNOWN").upper()
    oi_structure = first_text(texts, ["oi_structure"], "UNKNOWN").upper()
    flow_direction = first_text(texts, ["flow_direction", "direction"], direction).upper()
    flow_authority = first_text(texts, ["flow_authority", "authority"], "UNKNOWN").upper()
    flow_risk = first_text(texts, ["flow_risk", "risk"], "UNKNOWN").upper()
    flow_strength = first_text(texts, ["flow_strength", "strength"], "UNKNOWN").upper()

    score = 0
    max_score = 0
    tags: List[str] = []
    reasons: List[str] = []

    direction_ok = (side == "LONG" and direction == "LONG_ONLY") or (side == "SHORT" and direction == "SHORT_ONLY")
    location_ok = (side == "LONG" and zone == "DISCOUNT") or (side == "SHORT" and zone == "PREMIUM")
    flow_direction_ok = (side == "LONG" and flow_direction == "LONG_ONLY") or (side == "SHORT" and flow_direction == "SHORT_ONLY")

    max_score += 2
    if direction_ok:
        score += 2
        tags.append("DIRECTION_OK")
    else:
        tags.append("DIRECTION_FAIL")

    max_score += 2
    if location_ok:
        score += 2
        tags.append("LOCATION_OK")
    else:
        tags.append("LOCATION_FAIL")

    max_score += 2
    if flow_direction_ok:
        score += 2
        tags.append("FLOW_DIRECTION_OK")
    elif flow_direction in {"NO_TRADE", "UNKNOWN"}:
        tags.append("FLOW_DIRECTION_NOT_READY")
    else:
        score += 1
        tags.append("FLOW_DIRECTION_PARTIAL_OR_MISMATCH")

    # CVD confirmation
    max_score += 3
    if side == "LONG":
        if cvd_delta_15m > 0 and cvd_z > 0:
            score += 3
            tags.append("CVD_STRONG_LONG")
        elif cvd_delta_15m > 0 or cvd_z > 0:
            score += 2
            tags.append("CVD_PARTIAL_LONG")
        else:
            tags.append("CVD_NOT_SUPPORT_LONG")
            reasons.append("CVD_NOT_SUPPORTIVE")
    else:
        if cvd_delta_15m < 0 and cvd_z < 0:
            score += 3
            tags.append("CVD_STRONG_SHORT")
        elif cvd_delta_15m < 0 or cvd_z < 0:
            score += 2
            tags.append("CVD_PARTIAL_SHORT")
        else:
            tags.append("CVD_NOT_SUPPORT_SHORT")
            reasons.append("CVD_NOT_SUPPORTIVE")

    # OI confirmation
    max_score += 3
    if oi_delta_15m > 0 and oi_delta_1h >= 0:
        score += 3
        tags.append("OI_EXPANSION_ALIGNED")
    elif oi_delta_15m > 0:
        score += 2
        tags.append("OI_EXPANSION_SHORT_TERM")
    elif oi_delta_15m == 0 and oi_delta_1h == 0:
        tags.append("OI_FLAT_OR_MISSING_VALUE")
        reasons.append("OI_NOT_EXPANDING")
    else:
        tags.append("OI_CONTRACTION")
        reasons.append("OI_NOT_EXPANDING")

    # Flow authority / risk
    max_score += 2
    if flow_authority == "ENTRY_ELIGIBLE":
        score += 2
        tags.append("FLOW_AUTHORITY_ENTRY_ELIGIBLE")
    elif flow_authority == "WATCH_ONLY":
        score += 1
        tags.append("FLOW_AUTHORITY_WATCH_ONLY")
    else:
        tags.append("FLOW_AUTHORITY_NOT_READY")

    max_score += 2
    if "TRAP" in flow_risk:
        tags.append("FLOW_TRAP_RISK")
        reasons.append("FLOW_TRAP_RISK")
    elif "STRONG" in flow_strength:
        score += 2
        tags.append("FLOW_STRENGTH_STRONG")
    elif flow_strength not in {"UNKNOWN", "NO_FLOW"}:
        score += 1
        tags.append("FLOW_STRENGTH_PARTIAL")
    else:
        tags.append("FLOW_STRENGTH_WEAK_OR_UNKNOWN")

    # Funding only as soft signal because scale can vary.
    max_score += 1
    if side == "LONG":
        if funding <= 0.02:
            score += 1
            tags.append("FUNDING_NOT_TOO_EXPENSIVE_LONG")
        else:
            tags.append("FUNDING_EXPENSIVE_LONG")
    else:
        if funding >= -0.02:
            score += 1
            tags.append("FUNDING_NOT_TOO_EXPENSIVE_SHORT")
        else:
            tags.append("FUNDING_EXPENSIVE_SHORT")

    # RSI explicitly missing.
    tags.append("RSI_MISSING_EXPLICIT_NOT_SCORED")
    reasons.append("RSI_MISSING_EXPLICIT")

    ratio = round(score / max_score, 4) if max_score else 0.0

    # Outcome labels from F2X/F2Y, for audit simulation.
    o3 = f2x_row.get("outcome_3c", {}) if isinstance(f2x_row, dict) else {}
    o6 = f2x_row.get("outcome_6c", {}) if isinstance(f2x_row, dict) else {}
    mfe3 = as_float(o3.get("mfe_pct"))
    mae3 = as_float(o3.get("mae_pct"))
    close3 = as_float(o3.get("close_return_pct"))
    mfe6 = as_float(o6.get("mfe_pct"))
    mae6 = as_float(o6.get("mae_pct"))
    close6 = as_float(o6.get("close_return_pct"))

    if mae3 < 0 and abs(mae3) > max(mfe3, 0.0001):
        tags.append("OUTCOME_3C_MAE_DOMINATES_MFE")
    if close3 < 0:
        tags.append("OUTCOME_3C_CLOSE_NEGATIVE")
    if close6 < 0:
        tags.append("OUTCOME_6C_CLOSE_NEGATIVE")
    if mae3 <= -0.50:
        tags.append("OUTCOME_3C_MAE_LT_NEG_050")
    if mae3 <= -0.70:
        tags.append("OUTCOME_3C_MAE_LT_NEG_070")

    hard_fail = any(x in tags for x in [
        "DIRECTION_FAIL",
        "LOCATION_FAIL",
        "FLOW_TRAP_RISK",
        "CVD_NOT_SUPPORT_LONG",
        "CVD_NOT_SUPPORT_SHORT",
        "OI_CONTRACTION",
    ])

    # Audit classification, not live behavior.
    if hard_fail:
        hardened_status = "HARDENED_FAIL_SIGNAL_CONFLICT"
    elif ratio >= 0.72 and "OUTCOME_3C_MAE_DOMINATES_MFE" not in tags and close3 >= 0:
        hardened_status = "HARDENED_PASS_SHADOW_AUDIT"
    elif ratio >= 0.62 and close3 >= -0.10 and mae3 >= -0.50:
        hardened_status = "HARDENED_PARTIAL_SHADOW_AUDIT"
    else:
        hardened_status = "HARDENED_REJECT_SHADOW_AUDIT"

    return {
        "pair": pair,
        "side": side,
        "regime": regime,
        "zone": zone,
        "direction": direction,
        "flow_direction": flow_direction,
        "flow_authority": flow_authority,
        "flow_risk": flow_risk,
        "flow_strength": flow_strength,
        "cvd_delta_15m": cvd_delta_15m,
        "cvd_z": cvd_z,
        "cvd_structure": cvd_structure,
        "oi_delta_15m": oi_delta_15m,
        "oi_delta_1h": oi_delta_1h,
        "oi_structure": oi_structure,
        "funding": funding,
        "score": score,
        "max_score": max_score,
        "ratio": ratio,
        "hardened_status": hardened_status,
        "f2y_status": norm(f2y_row.get("status")),
        "mfe3": mfe3,
        "mae3": mae3,
        "close3": close3,
        "mfe6": mfe6,
        "mae6": mae6,
        "close6": close6,
        "tags": tags,
        "reasons": sorted(set(reasons)),
    }


def simulate(results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    rules = [
        "PASS_HARDENED_STATUS_ONLY",
        "PASS_RATIO_GE_072",
        "PASS_RATIO_GE_072_AND_3C_CLOSE_NONNEG",
        "PASS_RATIO_GE_072_AND_NO_MAE_DOMINATES_MFE",
        "PASS_RATIO_GE_062_AND_3C_MAE_GE_NEG050",
    ]

    out = []
    for rule in rules:
        passed = []
        for r in results:
            tags = set(r.get("tags", []))
            ratio = as_float(r.get("ratio"))
            close3 = as_float(r.get("close3"))
            mae3 = as_float(r.get("mae3"))

            if rule == "PASS_HARDENED_STATUS_ONLY":
                ok = r.get("hardened_status") in {"HARDENED_PASS_SHADOW_AUDIT", "HARDENED_PARTIAL_SHADOW_AUDIT"}
            elif rule == "PASS_RATIO_GE_072":
                ok = ratio >= 0.72
            elif rule == "PASS_RATIO_GE_072_AND_3C_CLOSE_NONNEG":
                ok = ratio >= 0.72 and close3 >= 0
            elif rule == "PASS_RATIO_GE_072_AND_NO_MAE_DOMINATES_MFE":
                ok = ratio >= 0.72 and "OUTCOME_3C_MAE_DOMINATES_MFE" not in tags
            elif rule == "PASS_RATIO_GE_062_AND_3C_MAE_GE_NEG050":
                ok = ratio >= 0.62 and mae3 >= -0.50
            else:
                ok = False

            if ok:
                passed.append(r)

        def avg(vals: List[float]) -> float:
            return round(sum(vals) / len(vals), 4) if vals else 0.0

        out.append({
            "rule": rule,
            "pass_count": len(passed),
            "drop_count": len(results) - len(passed),
            "passed_pairs": [f"{x['pair']}|{x['side']}" for x in passed],
            "avg3_mfe": avg([as_float(x.get("mfe3")) for x in passed]),
            "avg3_mae": avg([as_float(x.get("mae3")) for x in passed]),
            "avg3_close": avg([as_float(x.get("close3")) for x in passed]),
            "avg6_close": avg([as_float(x.get("close6")) for x in passed]),
        })
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runtime-dir", default="user_data/revo_alpha/runtime/bybit")
    args = ap.parse_args()

    runtime = Path(args.runtime_dir)

    f2z_b = load_json(runtime / "revo_f2z_b_pair_bound_telemetry_mapper_state.json", {})
    f2w_b = load_json(runtime / "revo_f2w_b_trigger_field_score_state.json", {})
    f2x = load_json(runtime / "revo_f2x_shadow_outcome_state.json", {})
    f2y = load_json(runtime / "revo_f2y_trigger_failure_attribution_state.json", {})

    flow_context = load_json(runtime / "revo_flow_context.json", {})
    execution_context = load_json(runtime / "revo_execution_context.json", {})
    collector = load_json(runtime / "revo_flow_context_collector.json", {})

    candidates = f2z_b.get("candidate_reports", []) if isinstance(f2z_b, dict) else []
    f2w_lookup = f2w_keyed(f2w_b)
    f2x_lookup = outcome_keyed(f2x)
    f2y_lookup = f2y_keyed(f2y)

    results = []
    status_counts = Counter()
    tag_counts = Counter()
    reason_counts = Counter()

    for c in candidates:
        pair = norm(c.get("pair"))
        side = norm(c.get("side")).upper()
        key = f"{pair}|{side}"

        flow_obj = get_pair_obj(flow_context, pair) or {}
        exec_obj = get_pair_obj(execution_context, pair) or {}
        collector_obj = get_pair_obj(collector, pair) or {}

        scored = score_pair(
            c,
            flow_obj,
            exec_obj,
            collector_obj,
            f2w_lookup.get(key, {}),
            f2x_lookup.get(key, {}),
            f2y_lookup.get(key, {}),
        )
        results.append(scored)
        status_counts[scored["hardened_status"]] += 1
        for t in scored.get("tags", []):
            tag_counts[t] += 1
        for r in scored.get("reasons", []):
            reason_counts[r] += 1

    simulations = simulate(results)

    # Conservative decision: audit-only and no live promotion.
    if status_counts.get("HARDENED_PASS_SHADOW_AUDIT", 0) >= 3:
        decision = "HARDENED_SCORER_PROMISING_BUT_NEEDS_MORE_OUTCOME_BATCH"
    elif status_counts.get("HARDENED_PASS_SHADOW_AUDIT", 0) >= 1:
        decision = "HARDENED_SCORER_SELECTIVE_ONLY_MORE_BATCH_REQUIRED"
    else:
        decision = "HARDENED_SCORER_NOT_READY"

    primary_recommendations = [
        "NO_ENTRY_PROMOTION_FROM_CURRENT_SAMPLE",
        "USE_PAIR_BOUND_OI_CVD_IN_TRIGGER_SCORER_AUDIT_ONLY",
        "RSI_MISSING_EXPLICIT_NOT_SCORED",
        "REJECT_IF_CVD_NOT_SUPPORTIVE",
        "REJECT_IF_OI_CONTRACTION_OR_NOT_EXPANDING",
        "REJECT_IF_FLOW_TRAP_RISK",
        "REQUIRE_3C_CLOSE_NONNEGATIVE_OR_NO_MAE_DOMINATES_MFE_FOR_PROMOTION",
        "RUN_NEXT_OUTCOME_BATCH_BEFORE_ANY_BEHAVIOR_PATCH",
    ]

    payload = {
        "event": "F2Z_C_PAIR_BOUND_HARDENED_TRIGGER_SCORER",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "runtime_dir": str(runtime),
        "candidate_count": len(results),
        "decision": decision,
        "status_counts": status_counts.most_common(),
        "tag_counts": tag_counts.most_common(),
        "reason_counts": reason_counts.most_common(),
        "rule_simulations": simulations,
        "results": results,
        "primary_recommendations": primary_recommendations,
        "behavior_change": "NONE",
        "entry_gate_change": "NONE",
        "risk_change": "NONE",
    }

    out_state = runtime / "revo_f2z_c_pair_bound_hardened_trigger_scorer_state.json"
    out_compact_runtime = runtime / "F2Z_C_PAIR_BOUND_HARDENED_TRIGGER_SCORER_COMPACT.txt"
    out_compact_root = Path("F2Z_C_PAIR_BOUND_HARDENED_TRIGGER_SCORER_COMPACT.txt")

    out_state.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    lines = []
    lines.append("F2Z_C_PAIR_BOUND_HARDENED_TRIGGER_SCORER_COMPACT")
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

    lines.append("TOP_TAGS")
    for k, v in tag_counts.most_common(40):
        lines.append(f"{k}={v}")
    lines.append("")

    lines.append("REASONS")
    for k, v in reason_counts.most_common():
        lines.append(f"{k}={v}")
    lines.append("")

    lines.append("PAIR_SCORES")
    for r in results:
        lines.append(
            "|".join([
                str(r["hardened_status"]),
                str(r["pair"]),
                str(r["side"]),
                f"f2y={r['f2y_status']}",
                f"regime={r['regime']}",
                f"zone={r['zone']}",
                f"direction={r['direction']}",
                f"flow_direction={r['flow_direction']}",
                f"flow_authority={r['flow_authority']}",
                f"flow_risk={r['flow_risk']}",
                f"flow_strength={r['flow_strength']}",
                f"score={r['score']}/{r['max_score']}",
                f"ratio={r['ratio']}",
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

    lines.append("RULE_SIMULATION")
    for s in simulations:
        lines.append(
            "|".join([
                s["rule"],
                f"pass={s['pass_count']}",
                f"drop={s['drop_count']}",
                f"avg3_mfe={s['avg3_mfe']}",
                f"avg3_mae={s['avg3_mae']}",
                f"avg3_close={s['avg3_close']}",
                f"avg6_close={s['avg6_close']}",
                f"passed={','.join(s['passed_pairs'])}",
            ])
        )
    lines.append("")

    lines.append("PRIMARY_RECOMMENDATIONS")
    for r in primary_recommendations:
        lines.append(f"- {r}")
    lines.append("")

    lines.append("DECISION")
    lines.append("NO_ENTRY_GATE_RISK_CHANGE")
    lines.append("NEXT_STEP_DEPENDS_ON_PASS_COUNT_AND_OUTCOME_SIMULATION")
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
