#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Dict, List, Tuple


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


def get_outcome(item: Dict[str, Any], key: str) -> Dict[str, Any]:
    out = item.get(key, {})
    if isinstance(out, dict):
        return out
    return {}


def ok_outcome(out: Dict[str, Any]) -> bool:
    return norm(out.get("status")) == "OK"


def signed_abs(v: float) -> float:
    return abs(v)


def classify_candidate(item: Dict[str, Any], f2w_lookup: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    pair = norm(item.get("pair"))
    side = norm(item.get("side")).upper()
    key = f"{pair}|{side}"

    o1 = get_outcome(item, "outcome_1c")
    o3 = get_outcome(item, "outcome_3c")
    o6 = get_outcome(item, "outcome_6c")

    f2w = f2w_lookup.get(key, {})
    mapped_groups = f2w.get("f2w_b_mapped_groups") or []
    if not isinstance(mapped_groups, list):
        mapped_groups = []

    tags: List[str] = []
    hardening: List[str] = []

    if not (ok_outcome(o1) and ok_outcome(o3) and ok_outcome(o6)):
        tags.append("OUTCOME_DATA_INCOMPLETE")
        hardening.append("REQUIRE_COMPLETE_OUTCOME_DATA_BEFORE_PROMOTION")
        return {
            "pair": pair,
            "side": side,
            "status": "DATA_INCOMPLETE",
            "tags": tags,
            "hardening": hardening,
            "mapped_groups": mapped_groups,
        }

    mfe1 = as_float(o1.get("mfe_pct"))
    mae1 = as_float(o1.get("mae_pct"))
    close1 = as_float(o1.get("close_return_pct"))

    mfe3 = as_float(o3.get("mfe_pct"))
    mae3 = as_float(o3.get("mae_pct"))
    close3 = as_float(o3.get("close_return_pct"))

    mfe6 = as_float(o6.get("mfe_pct"))
    mae6 = as_float(o6.get("mae_pct"))
    close6 = as_float(o6.get("close_return_pct"))

    regime = norm(item.get("regime")).upper()
    zone = norm(item.get("pd_zone")).upper()
    direction = norm(item.get("direction")).upper()

    if signed_abs(mae3) > max(mfe3, 0.0001):
        tags.append("MAE_DOMINATES_MFE_3C")
    if signed_abs(mae6) > max(mfe6, 0.0001):
        tags.append("MAE_DOMINATES_MFE_6C")
    if close1 < 0:
        tags.append("NEGATIVE_CLOSE_1C")
    if close3 < 0:
        tags.append("NEGATIVE_CLOSE_3C")
    if close6 < 0:
        tags.append("NEGATIVE_CLOSE_6C")
    if mae1 <= -0.30:
        tags.append("HIT_NEG_03_1C")
    if mae3 <= -0.30:
        tags.append("HIT_NEG_03_3C")
    if mae6 <= -0.30:
        tags.append("HIT_NEG_03_6C")
    if mae1 <= -0.50:
        tags.append("HIT_NEG_05_1C")
    if mae3 <= -0.50:
        tags.append("HIT_NEG_05_3C")
    if mae6 <= -0.50:
        tags.append("HIT_NEG_05_6C")
    if mfe3 < 0.30:
        tags.append("LOW_MFE_3C_LT_030")
    if mfe6 < 0.30:
        tags.append("LOW_MFE_6C_LT_030")
    if mfe3 >= 0.30 and close6 < 0:
        tags.append("FADE_AFTER_INITIAL_MFE")
    if "RANGING" in regime and (close3 <= 0 or mfe3 < 0.30):
        tags.append("RANGING_WEAK_BOUNCE")
    if "TRENDING" in regime and (mae1 <= -0.50 or mae3 <= -0.70):
        tags.append("TRENDING_HIGH_MAE")
    if "cvd" not in mapped_groups:
        tags.append("MISSING_CVD_TRIGGER_MAPPING")
    if "oi" not in mapped_groups:
        tags.append("MISSING_OI_TRIGGER_MAPPING")
    if "rsi" not in mapped_groups:
        tags.append("MISSING_RSI_TRIGGER_MAPPING")

    if "MISSING_CVD_TRIGGER_MAPPING" in tags or "MISSING_OI_TRIGGER_MAPPING" in tags:
        hardening.append("REQUIRE_CVD_OI_CONFIRMATION_OR_EXPLICIT_MAPPING_BEFORE_PROMOTION")
    if "MISSING_RSI_TRIGGER_MAPPING" in tags:
        hardening.append("REQUIRE_RSI_RESET_TURN_CONFIRMATION_BEFORE_PROMOTION")
    if "RANGING_WEAK_BOUNCE" in tags:
        hardening.append("RANGING_REQUIRE_POST_TRIGGER_CLOSE_CONFIRMATION")
    if "TRENDING_HIGH_MAE" in tags:
        hardening.append("TRENDING_REJECT_IF_1C_MAE_LT_NEG_050")
    if "MAE_DOMINATES_MFE_3C" in tags:
        hardening.append("REJECT_IF_3C_MAE_DOMINATES_MFE")
    if "NEGATIVE_CLOSE_3C" in tags and "LOW_MFE_3C_LT_030" in tags:
        hardening.append("REQUIRE_3C_MFE_GE_030_OR_3C_CLOSE_NONNEGATIVE")
    if "FADE_AFTER_INITIAL_MFE" in tags:
        hardening.append("REQUIRE_EXIT_OR_INVALIDATION_IF_6C_CLOSE_FADES_NEGATIVE")

    # Outcome classification. This is audit-only, not a live rule.
    if close3 > 0 and mae3 >= -0.35 and mfe3 >= 0.30:
        status = "MICRO_EDGE_PASS"
    elif close6 > 0 and mae6 >= -0.35:
        status = "DELAYED_MICRO_EDGE_PARTIAL"
    elif mae3 <= -0.70 or mae6 <= -0.70:
        status = "FAIL_MAE_TOO_LARGE"
    elif close3 < 0 and close6 < 0:
        status = "FAIL_CLOSE_NEGATIVE"
    elif mfe3 < 0.30 and mfe6 < 0.30:
        status = "FAIL_MFE_TOO_SMALL"
    else:
        status = "WEAK_OR_MIXED"

    if not hardening:
        hardening.append("KEEP_WATCH_ONLY_NO_HARDENING_RULE_SELECTED")

    return {
        "pair": pair,
        "side": side,
        "status": status,
        "regime": regime,
        "zone": zone,
        "direction": direction,
        "mapped_groups": mapped_groups,
        "mfe1": mfe1,
        "mae1": mae1,
        "close1": close1,
        "mfe3": mfe3,
        "mae3": mae3,
        "close3": close3,
        "mfe6": mfe6,
        "mae6": mae6,
        "close6": close6,
        "tags": tags,
        "hardening": hardening,
    }


def avg(values: List[float]) -> float:
    if not values:
        return 0.0
    return round(sum(values) / len(values), 4)


def simulate_rule(results: List[Dict[str, Any]], rule_name: str) -> Dict[str, Any]:
    passed = []

    for r in results:
        tags = set(r.get("tags", []))

        if rule_name == "DROP_1C_MAE_LT_NEG_050":
            ok = "HIT_NEG_05_1C" not in tags
        elif rule_name == "REQUIRE_3C_MAE_GE_NEG_040":
            ok = as_float(r.get("mae3")) >= -0.40
        elif rule_name == "REQUIRE_3C_CLOSE_GE_NEG_010":
            ok = as_float(r.get("close3")) >= -0.10
        elif rule_name == "REQUIRE_3C_MFE_GE_030":
            ok = as_float(r.get("mfe3")) >= 0.30
        elif rule_name == "RANGING_REQUIRE_3C_CLOSE_NONNEG_OR_MFE_GE_030":
            if "RANGING" in norm(r.get("regime")).upper():
                ok = as_float(r.get("close3")) >= 0.0 or as_float(r.get("mfe3")) >= 0.30
            else:
                ok = True
        elif rule_name == "REQUIRE_NO_MAE_DOMINATES_MFE_3C":
            ok = "MAE_DOMINATES_MFE_3C" not in tags
        elif rule_name == "STRICT_PROMOTION_3C_MFE_GE_030_AND_CLOSE_POSITIVE":
            ok = as_float(r.get("mfe3")) >= 0.30 and as_float(r.get("close3")) > 0
        else:
            ok = True

        if ok:
            passed.append(r)

    return {
        "rule": rule_name,
        "pass_count": len(passed),
        "drop_count": len(results) - len(passed),
        "avg_3c_mfe": avg([as_float(x.get("mfe3")) for x in passed]),
        "avg_3c_mae": avg([as_float(x.get("mae3")) for x in passed]),
        "avg_3c_close": avg([as_float(x.get("close3")) for x in passed]),
        "avg_6c_mfe": avg([as_float(x.get("mfe6")) for x in passed]),
        "avg_6c_mae": avg([as_float(x.get("mae6")) for x in passed]),
        "avg_6c_close": avg([as_float(x.get("close6")) for x in passed]),
        "passed_pairs": [f"{x.get('pair')}|{x.get('side')}" for x in passed],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runtime-dir", default="user_data/revo_alpha/runtime/bybit")
    args = ap.parse_args()

    runtime = Path(args.runtime_dir)

    f2x_path = runtime / "revo_f2x_shadow_outcome_state.json"
    f2w_path = runtime / "revo_f2w_b_trigger_field_score_state.json"

    out_state = runtime / "revo_f2y_trigger_failure_attribution_state.json"
    out_compact_runtime = runtime / "F2Y_TRIGGER_FAILURE_ATTRIBUTION_COMPACT.txt"
    out_compact_root = Path("F2Y_TRIGGER_FAILURE_ATTRIBUTION_COMPACT.txt")

    f2x = load_json(f2x_path, {})
    f2w = load_json(f2w_path, {})

    f2w_lookup: Dict[str, Dict[str, Any]] = {}
    for row in f2w.get("rows", []) if isinstance(f2w, dict) else []:
        pair = norm(row.get("pair"))
        side = norm(row.get("side")).upper()
        f2w_lookup[f"{pair}|{side}"] = row

    source_results = f2x.get("results", []) if isinstance(f2x, dict) else []

    classified = []
    status_counts = Counter()
    tag_counts = Counter()
    hardening_counts = Counter()
    regime_counts = Counter()
    mapped_counts = Counter()

    for item in source_results:
        c = classify_candidate(item, f2w_lookup)
        classified.append(c)
        status_counts[c.get("status")] += 1
        regime_counts[c.get("regime")] += 1
        for t in c.get("tags", []):
            tag_counts[t] += 1
        for h in c.get("hardening", []):
            hardening_counts[h] += 1
        for m in c.get("mapped_groups", []):
            mapped_counts[m] += 1

    rule_names = [
        "DROP_1C_MAE_LT_NEG_050",
        "REQUIRE_3C_MAE_GE_NEG_040",
        "REQUIRE_3C_CLOSE_GE_NEG_010",
        "REQUIRE_3C_MFE_GE_030",
        "RANGING_REQUIRE_3C_CLOSE_NONNEG_OR_MFE_GE_030",
        "REQUIRE_NO_MAE_DOMINATES_MFE_3C",
        "STRICT_PROMOTION_3C_MFE_GE_030_AND_CLOSE_POSITIVE",
    ]
    simulations = [simulate_rule(classified, name) for name in rule_names]

    # Pick conservative recommendations based on this small sample.
    primary_recommendations = [
        "NO_ENTRY_PROMOTION_FROM_CURRENT_SAMPLE",
        "KEEP_TRIGGER_CONFIRMED_AS_SHADOW_ONLY",
        "ADD_CVD_OI_RSI_HARD_CONFIRMATION_MAPPING_BEFORE_ANY_PROMOTION",
        "FOR_RANGING_LONG_REQUIRE_POST_TRIGGER_CLOSE_CONFIRMATION",
        "REJECT_TRIGGER_PROMOTION_IF_1C_MAE_LT_NEG_050",
        "REJECT_TRIGGER_PROMOTION_IF_3C_MAE_DOMINATES_MFE",
        "USE_OUTCOME_TRACKER_ON_NEXT_BATCH_BEFORE_BEHAVIOR_PATCH",
    ]

    payload = {
        "event": "F2Y_TRIGGER_FAILURE_ATTRIBUTION",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "runtime_dir": str(runtime),
        "source_f2x": str(f2x_path),
        "source_f2w_b": str(f2w_path),
        "candidate_count": len(classified),
        "status_counts": status_counts.most_common(),
        "tag_counts": tag_counts.most_common(),
        "hardening_counts": hardening_counts.most_common(),
        "regime_counts": regime_counts.most_common(),
        "mapped_group_counts": mapped_counts.most_common(),
        "rule_simulations": simulations,
        "primary_recommendations": primary_recommendations,
        "classified": classified,
        "behavior_change": "NONE",
        "entry_gate_change": "NONE",
        "risk_change": "NONE",
    }

    out_state.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    lines = []
    lines.append("F2Y_TRIGGER_FAILURE_ATTRIBUTION_COMPACT")
    lines.append(f"generated_at={payload['generated_at']}")
    lines.append(f"runtime_dir={runtime}")
    lines.append(f"candidate_count={len(classified)}")
    lines.append("behavior_change=NONE")
    lines.append("entry_gate_change=NONE")
    lines.append("risk_change=NONE")
    lines.append("")

    lines.append("STATUS_COUNTS")
    for k, v in status_counts.most_common():
        lines.append(f"{k}={v}")
    lines.append("")

    lines.append("REGIME_COUNTS")
    for k, v in regime_counts.most_common():
        lines.append(f"{k}={v}")
    lines.append("")

    lines.append("MAPPED_GROUP_COUNTS")
    for k, v in mapped_counts.most_common():
        lines.append(f"{k}={v}")
    lines.append("")

    lines.append("TOP_FAILURE_TAGS")
    for k, v in tag_counts.most_common():
        lines.append(f"{k}={v}")
    lines.append("")

    lines.append("HARDENING_COUNTS")
    for k, v in hardening_counts.most_common():
        lines.append(f"{k}={v}")
    lines.append("")

    lines.append("CANDIDATE_ATTRIBUTION")
    for c in classified:
        lines.append(
            "|".join([
                str(c.get("status")),
                str(c.get("pair")),
                str(c.get("side")),
                f"regime={c.get('regime')}",
                f"zone={c.get('zone')}",
                f"direction={c.get('direction')}",
                f"mfe3={c.get('mfe3')}",
                f"mae3={c.get('mae3')}",
                f"close3={c.get('close3')}",
                f"mfe6={c.get('mfe6')}",
                f"mae6={c.get('mae6')}",
                f"close6={c.get('close6')}",
                f"mapped={','.join(c.get('mapped_groups', []))}",
                f"tags={','.join(c.get('tags', []))}",
                f"hardening={','.join(c.get('hardening', []))}",
            ])
        )
    lines.append("")

    lines.append("RULE_SIMULATION")
    for sim in simulations:
        lines.append(
            "|".join([
                sim["rule"],
                f"pass={sim['pass_count']}",
                f"drop={sim['drop_count']}",
                f"avg3_mfe={sim['avg_3c_mfe']}",
                f"avg3_mae={sim['avg_3c_mae']}",
                f"avg3_close={sim['avg_3c_close']}",
                f"avg6_mfe={sim['avg_6c_mfe']}",
                f"avg6_mae={sim['avg_6c_mae']}",
                f"avg6_close={sim['avg_6c_close']}",
                f"passed={','.join(sim['passed_pairs'])}",
            ])
        )
    lines.append("")

    lines.append("PRIMARY_RECOMMENDATIONS")
    for r in primary_recommendations:
        lines.append(f"- {r}")
    lines.append("")

    lines.append("DECISION")
    lines.append("REJECT_ENTRY_PROMOTION_CURRENT_SAMPLE")
    lines.append("KEEP_F2U_F2V_F2W_AS_AUDIT_PIPELINE")
    lines.append("NEXT_PATCH_SHOULD_BE_TRIGGER_HARDENING_TELEMETRY_OR_OUTCOME_TRACKER_BATCH")
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
