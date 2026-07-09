#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Dict, Tuple


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


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return default


def flatten_trigger_fields(fields: Dict[str, Any]) -> Dict[str, Any]:
    out = {}
    for k, v in fields.items():
        if isinstance(v, dict):
            for kk, vv in v.items():
                out[f"{k}.{kk}"] = vv
        else:
            out[k] = v
    return out


def find_numeric(fields: Dict[str, Any], needles: list[str]) -> list[Tuple[str, float]]:
    found = []
    for k, v in fields.items():
        lk = k.lower()
        if any(n in lk for n in needles):
            try:
                found.append((k, float(v)))
            except Exception:
                continue
    return found


def find_text(fields: Dict[str, Any], needles: list[str]) -> list[Tuple[str, str]]:
    found = []
    for k, v in fields.items():
        lk = k.lower()
        if any(n in lk for n in needles):
            found.append((k, str(v)))
    return found


def score_trigger(row: Dict[str, Any]) -> Dict[str, Any]:
    pair = norm(row.get("pair"))
    side = norm(row.get("side")).upper()
    status = norm(row.get("f2w_status"))
    setup_state = norm(row.get("setup_state"))
    zone = norm(row.get("pd_zone")).upper()
    direction = norm(row.get("direction_engine")).upper()
    regime = norm(row.get("regime_router")).upper()
    fields = flatten_trigger_fields(row.get("trigger_fields") or {})

    notes = []
    score = 0
    max_score = 0
    mapped_groups = Counter()

    if status == "MISMATCH_AUDIT":
        return {
            "pair": pair,
            "side": side,
            "trigger_score": 0,
            "trigger_max_score": 0,
            "trigger_score_ratio": 0.0,
            "trigger_status": "MISMATCH_AUDIT",
            "trigger_reason": "PAIR_LEVEL_SCORE_GATE_MISMATCH_AUDIT_REQUIRED",
            "mapped_groups": [],
            "notes": ["Do not treat as trigger candidate until mismatch is audited."],
            "trigger_keys": list(fields.keys()),
            "trigger_fields": fields,
        }

    if status == "WAIT_LOCATION" or setup_state == "SETUP_VALID_WAIT_LOCATION":
        return {
            "pair": pair,
            "side": side,
            "trigger_score": 0,
            "trigger_max_score": 0,
            "trigger_score_ratio": 0.0,
            "trigger_status": "WAIT_LOCATION",
            "trigger_reason": "LOCATION_NOT_READY_DO_NOT_SCORE_TRIGGER_AS_ENTRY",
            "mapped_groups": [],
            "notes": ["Location is not ideal yet. Keep watching, do not trigger-score for entry."],
            "trigger_keys": list(fields.keys()),
            "trigger_fields": fields,
        }

    direction_ok = (side == "LONG" and direction == "LONG_ONLY") or (side == "SHORT" and direction == "SHORT_ONLY")
    location_ok = (side == "LONG" and zone == "DISCOUNT") or (side == "SHORT" and zone == "PREMIUM")

    max_score += 2
    if direction_ok:
        score += 2
        notes.append("direction_ok")
    else:
        notes.append("direction_not_ok")

    max_score += 2
    if location_ok:
        score += 2
        notes.append("location_ok")
    else:
        notes.append("location_not_ok")

    rsi_values = find_numeric(fields, ["rsi"])
    if rsi_values:
        mapped_groups["rsi"] += 1
        max_score += 2
        avg_rsi = sum(v for _, v in rsi_values) / max(1, len(rsi_values))
        if side == "LONG":
            if 28 <= avg_rsi <= 55:
                score += 2
                notes.append(f"rsi_long_reset_ok:{avg_rsi:.2f}")
            elif avg_rsi < 70:
                score += 1
                notes.append(f"rsi_long_neutral:{avg_rsi:.2f}")
            else:
                notes.append(f"rsi_long_overbought:{avg_rsi:.2f}")
        else:
            if 45 <= avg_rsi <= 72:
                score += 2
                notes.append(f"rsi_short_reset_ok:{avg_rsi:.2f}")
            elif avg_rsi > 30:
                score += 1
                notes.append(f"rsi_short_neutral:{avg_rsi:.2f}")
            else:
                notes.append(f"rsi_short_oversold:{avg_rsi:.2f}")

    stoch_values = find_numeric(fields, ["stoch", "stochastic", "k_", "d_"])
    if stoch_values:
        mapped_groups["stoch"] += 1
        max_score += 2
        vals = [v for _, v in stoch_values]
        avg_stoch = sum(vals) / max(1, len(vals))
        if side == "LONG":
            if avg_stoch <= 45:
                score += 2
                notes.append(f"stoch_long_reset_ok:{avg_stoch:.2f}")
            elif avg_stoch <= 70:
                score += 1
                notes.append(f"stoch_long_neutral:{avg_stoch:.2f}")
            else:
                notes.append(f"stoch_long_high:{avg_stoch:.2f}")
        else:
            if avg_stoch >= 55:
                score += 2
                notes.append(f"stoch_short_reset_ok:{avg_stoch:.2f}")
            elif avg_stoch >= 30:
                score += 1
                notes.append(f"stoch_short_neutral:{avg_stoch:.2f}")
            else:
                notes.append(f"stoch_short_low:{avg_stoch:.2f}")

    cvd_values = find_numeric(fields, ["cvd"])
    if cvd_values:
        mapped_groups["cvd"] += 1
        max_score += 2
        avg_cvd = sum(v for _, v in cvd_values) / max(1, len(cvd_values))
        if side == "LONG" and avg_cvd > 0:
            score += 2
            notes.append(f"cvd_long_positive:{avg_cvd:.4f}")
        elif side == "SHORT" and avg_cvd < 0:
            score += 2
            notes.append(f"cvd_short_negative:{avg_cvd:.4f}")
        elif abs(avg_cvd) > 0:
            score += 1
            notes.append(f"cvd_present_but_not_ideal:{avg_cvd:.4f}")
        else:
            notes.append("cvd_flat")

    oi_values = find_numeric(fields, ["oi", "open_interest"])
    if oi_values:
        mapped_groups["oi"] += 1
        max_score += 2
        avg_oi = sum(v for _, v in oi_values) / max(1, len(oi_values))
        if avg_oi > 0:
            score += 2
            notes.append(f"oi_expansion:{avg_oi:.4f}")
        else:
            notes.append(f"oi_not_expanding:{avg_oi:.4f}")

    candle_texts = find_text(fields, ["candle", "rejection", "wick", "engulf", "reclaim", "support", "resistance", "demand", "supply"])
    if candle_texts:
        mapped_groups["candle"] += 1
        max_score += 2
        joined = " ".join(v.upper() for _, v in candle_texts)
        bullish_words = ["BULL", "RECLAIM", "SUPPORT", "DEMAND", "BUY", "LONG", "REJECTION_LOW"]
        bearish_words = ["BEAR", "REJECT", "RESISTANCE", "SUPPLY", "SELL", "SHORT", "REJECTION_HIGH"]
        if side == "LONG" and any(w in joined for w in bullish_words):
            score += 2
            notes.append("candle_long_supportive")
        elif side == "SHORT" and any(w in joined for w in bearish_words):
            score += 2
            notes.append("candle_short_supportive")
        else:
            score += 1
            notes.append("candle_present_unclear")

    timing_texts = find_text(fields, ["timing", "trigger"])
    if timing_texts:
        mapped_groups["timing"] += 1
        max_score += 2
        joined = " ".join(v.upper() for _, v in timing_texts)
        if any(x in joined for x in ["PASS", "OK", "READY", "ALLOW", "CONFIRM"]):
            score += 2
            notes.append("timing_text_supportive")
        elif any(x in joined for x in ["WAIT", "BLOCK", "DENY", "NOT_READY"]):
            notes.append("timing_text_not_ready")
        else:
            score += 1
            notes.append("timing_text_unclear")

    if "CHOP" in regime:
        max_score += 1
        notes.append("chop_regime_penalty")
    else:
        max_score += 1
        score += 1
        notes.append("regime_not_chop")

    if max_score == 0:
        ratio = 0.0
    else:
        ratio = score / max_score

    if not fields:
        trigger_status = "TRIGGER_FIELD_MAPPING_NEEDED"
        reason = "NO_TRIGGER_FIELDS_IN_STATE"
    elif not mapped_groups:
        trigger_status = "TRIGGER_FIELD_MAPPING_NEEDED"
        reason = "FIELDS_EXIST_BUT_NOT_MAPPED_TO_RSI_STOCH_CVD_OI_CANDLE_TIMING"
    elif ratio >= 0.75 and direction_ok and location_ok:
        trigger_status = "TRIGGER_CONFIRMED_SHADOW"
        reason = "DIRECTION_LOCATION_AND_TRIGGER_SCORE_HIGH"
    elif ratio >= 0.55 and direction_ok and location_ok:
        trigger_status = "TRIGGER_PARTIAL_SHADOW"
        reason = "DIRECTION_LOCATION_OK_BUT_TRIGGER_SCORE_PARTIAL"
    else:
        trigger_status = "TRIGGER_NOT_READY"
        reason = "TRIGGER_SCORE_LOW_OR_CONTEXT_NOT_READY"

    return {
        "pair": pair,
        "side": side,
        "trigger_score": score,
        "trigger_max_score": max_score,
        "trigger_score_ratio": round(ratio, 4),
        "trigger_status": trigger_status,
        "trigger_reason": reason,
        "mapped_groups": sorted(mapped_groups.keys()),
        "notes": notes,
        "trigger_keys": list(fields.keys()),
        "trigger_fields": fields,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runtime-dir", default="user_data/revo_alpha/runtime/bybit")
    args = ap.parse_args()

    runtime = Path(args.runtime_dir)
    src = runtime / "revo_f2w_trigger_confirmation_state.json"
    out_state = runtime / "revo_f2w_b_trigger_field_score_state.json"
    out_compact_runtime = runtime / "F2W_B_TRIGGER_FIELD_SCORER_COMPACT.txt"
    out_compact_root = Path("F2W_B_TRIGGER_FIELD_SCORER_COMPACT.txt")

    data = load_json(src, {})
    rows = data.get("rows", []) if isinstance(data, dict) else []

    scored = []
    status_counts = Counter()
    reason_counts = Counter()
    mapped_counts = Counter()
    key_counts = Counter()

    for row in rows:
        s = score_trigger(row)
        merged = dict(row)
        merged.update({
            "f2w_b_trigger_status": s["trigger_status"],
            "f2w_b_trigger_reason": s["trigger_reason"],
            "f2w_b_trigger_score": s["trigger_score"],
            "f2w_b_trigger_max_score": s["trigger_max_score"],
            "f2w_b_trigger_score_ratio": s["trigger_score_ratio"],
            "f2w_b_mapped_groups": s["mapped_groups"],
            "f2w_b_notes": s["notes"],
            "f2w_b_trigger_keys": s["trigger_keys"],
        })
        scored.append(merged)
        status_counts[s["trigger_status"]] += 1
        reason_counts[s["trigger_reason"]] += 1
        for g in s["mapped_groups"]:
            mapped_counts[g] += 1
        for k in s["trigger_keys"]:
            key_counts[k] += 1

    payload = {
        "event": "F2W_B_TRIGGER_FIELD_SCORER",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "runtime_dir": str(runtime),
        "source": str(src),
        "candidate_count": len(rows),
        "status_counts": status_counts.most_common(),
        "reason_counts": reason_counts.most_common(),
        "mapped_group_counts": mapped_counts.most_common(),
        "trigger_key_counts": key_counts.most_common(),
        "rows": scored,
        "behavior_change": "NONE",
        "entry_gate_change": "NONE",
        "risk_change": "NONE",
    }
    out_state.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    lines = []
    lines.append("F2W_B_TRIGGER_FIELD_SCORER_COMPACT")
    lines.append(f"generated_at={payload['generated_at']}")
    lines.append(f"runtime_dir={runtime}")
    lines.append(f"candidate_count={len(rows)}")
    lines.append("")
    lines.append("TRIGGER_STATUS_COUNTS")
    for k, v in status_counts.most_common():
        lines.append(f"{k}={v}")
    lines.append("")
    lines.append("TRIGGER_REASON_COUNTS")
    for k, v in reason_counts.most_common():
        lines.append(f"{k}={v}")
    lines.append("")
    lines.append("MAPPED_GROUP_COUNTS")
    for k, v in mapped_counts.most_common():
        lines.append(f"{k}={v}")
    lines.append("")
    lines.append("TRIGGER_KEY_COUNTS")
    for k, v in key_counts.most_common():
        lines.append(f"{k}={v}")
    lines.append("")
    lines.append("CANDIDATE_SCORES")
    for r in scored:
        lines.append(
            "|".join([
                str(r.get("f2w_b_trigger_status")),
                str(r.get("pair")),
                str(r.get("side")),
                str(r.get("setup_state")),
                f"score={r.get('f2w_b_trigger_score')}/{r.get('f2w_b_trigger_max_score')}",
                f"ratio={r.get('f2w_b_trigger_score_ratio')}",
                f"mapped={','.join(r.get('f2w_b_mapped_groups') or [])}",
                f"zone={r.get('pd_zone')}",
                f"direction={r.get('direction_engine')}",
                f"regime={r.get('regime_router')}",
                f"reason={r.get('f2w_b_trigger_reason')}",
                f"notes={';'.join(r.get('f2w_b_notes') or [])}",
            ])
        )
    lines.append("")
    lines.append("OUTPUT_FILES")
    lines.append(f"state={out_state}")
    lines.append(f"compact_runtime={out_compact_runtime}")
    lines.append(f"compact_root={out_compact_root}")
    lines.append("")
    lines.append("DECISION_HINT")
    lines.append("If TRIGGER_CONFIRMED_SHADOW appears, do not enter yet; build shadow outcome tracker first.")
    lines.append("If TRIGGER_PARTIAL_SHADOW dominates, define stricter trigger rules before behavior patch.")
    lines.append("If TRIGGER_FIELD_MAPPING_NEEDED dominates, inspect trigger keys and map them explicitly.")
    lines.append("If MISMATCH_AUDIT appears, isolate that pair separately.")
    lines.append("No entry/gate/risk behavior changed.")

    text = "\n".join(lines) + "\n"
    out_compact_runtime.write_text(text, encoding="utf-8")
    out_compact_root.write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
