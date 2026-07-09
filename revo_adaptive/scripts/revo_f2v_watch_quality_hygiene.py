#!/usr/bin/env python3
from __future__ import annotations

import json
import argparse
from pathlib import Path
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Dict, Tuple


GRADE_RANK = {
    "A+": 6,
    "A": 5,
    "B+": 4,
    "B": 3,
    "C": 2,
    "D": 1,
    "NONE": 0,
    "UNKNOWN": 0,
    "NA": 0,
    "": 0,
}

STATE_PRIORITY = {
    "READY_TO_ENTER": 100,
    "A_PLUS_MISMATCH_AUDIT": 90,
    "SETUP_VALID_WAIT_TRIGGER": 80,
    "SETUP_VALID_WAIT_LOCATION": 60,
    "WATCH_SETUP_FORMING": 40,
}


def norm(v: Any) -> str:
    if v is None:
        return "UNKNOWN"
    s = str(v).strip()
    return s if s else "UNKNOWN"


def rank_grade(v: Any) -> int:
    return GRADE_RANK.get(norm(v).upper(), 0)


def as_float(v: Any) -> float:
    try:
        return float(v or 0.0)
    except Exception:
        return 0.0


def keep_decision(item: Dict[str, Any]) -> Tuple[bool, str]:
    state = norm(item.get("setup_state"))
    side = norm(item.get("side")).upper()
    zone = norm(item.get("pd_zone")).upper()
    regime = norm(item.get("regime_router")).upper()

    shadow_grade = norm(item.get("shadow_grade")).upper()
    family_grade = norm(item.get("family_grade")).upper()
    shadow_score = as_float(item.get("shadow_score"))
    family_score = as_float(item.get("family_score"))

    shadow_rank = rank_grade(shadow_grade)
    family_rank = rank_grade(family_grade)
    best_rank = max(shadow_rank, family_rank)
    best_score = max(shadow_score, family_score)

    bad_current_location = (
        (side == "LONG" and zone == "PREMIUM") or
        (side == "SHORT" and zone == "DISCOUNT")
    )

    d_d_low = shadow_rank <= GRADE_RANK["D"] and family_rank <= GRADE_RANK["D"]
    chop_low = "CHOP" in regime and best_rank <= GRADE_RANK["C"]

    if state == "A_PLUS_MISMATCH_AUDIT":
        if best_rank >= GRADE_RANK["A"] or best_score >= 95:
            return True, "KEEP_A_PLUS_MISMATCH"
        return False, "DROP_MISMATCH_NOT_HIGH_QUALITY"

    if state == "SETUP_VALID_WAIT_TRIGGER":
        if best_score >= 95 and family_rank >= GRADE_RANK["B"]:
            return True, "KEEP_WAIT_TRIGGER_HIGH_SCORE"
        if best_rank >= GRADE_RANK["B+"] and best_score >= 85:
            return True, "KEEP_WAIT_TRIGGER_GOOD_GRADE"
        return False, "DROP_WAIT_TRIGGER_LOW_QUALITY"

    if state == "SETUP_VALID_WAIT_LOCATION":
        if d_d_low:
            return False, "DROP_WAIT_LOCATION_D_D_LOW_QUALITY"
        if chop_low:
            return False, "DROP_WAIT_LOCATION_CHOP_LOW_QUALITY"
        if bad_current_location and best_rank < GRADE_RANK["B+"] and best_score < 95:
            return False, "DROP_WAIT_LOCATION_BAD_LOCATION_LOW_QUALITY"
        if best_rank >= GRADE_RANK["B+"] or best_score >= 95:
            return True, "KEEP_WAIT_LOCATION_QUALITY_OK"
        return False, "DROP_WAIT_LOCATION_NOT_ENOUGH_EDGE"

    if state == "WATCH_SETUP_FORMING":
        if best_rank >= GRADE_RANK["A"] or best_score >= 95:
            return True, "KEEP_SETUP_FORMING_HIGH_QUALITY"
        return False, "DROP_SETUP_FORMING_LOW_QUALITY"

    if state == "READY_TO_ENTER":
        return True, "KEEP_READY_TO_ENTER"

    return False, "DROP_UNKNOWN_STATE"


def sort_key(item: Dict[str, Any]) -> Tuple[int, float, float]:
    return (
        STATE_PRIORITY.get(norm(item.get("setup_state")), 0),
        as_float(item.get("shadow_score")),
        as_float(item.get("family_score")),
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runtime-dir", default="user_data/revo_alpha/runtime/bybit")
    ap.add_argument("--max-watch", type=int, default=30)
    args = ap.parse_args()

    runtime = Path(args.runtime_dir)
    src = runtime / "revo_f2v_watching_state.json"
    out_state = runtime / "revo_f2v_watch_quality_state.json"
    out_compact_runtime = runtime / "F2V_B_WATCH_QUALITY_HYGIENE_COMPACT.txt"
    out_compact_root = Path("F2V_B_WATCH_QUALITY_HYGIENE_COMPACT.txt")

    if not src.exists():
        raise SystemExit(f"ERROR: missing {src}")

    data = json.loads(src.read_text(encoding="utf-8", errors="replace"))
    watch = data.get("watch", {})
    if not isinstance(watch, dict):
        watch = {}

    kept = {}
    dropped = {}
    keep_reasons = Counter()
    drop_reasons = Counter()

    for key, item in watch.items():
        ok, reason = keep_decision(item)
        item = dict(item)
        item["f2v_b_quality_decision"] = "KEEP" if ok else "DROP"
        item["f2v_b_quality_reason"] = reason
        if ok:
            kept[key] = item
            keep_reasons[reason] += 1
        else:
            dropped[key] = item
            drop_reasons[reason] += 1

    ranked = sorted(kept.items(), key=lambda kv: sort_key(kv[1]), reverse=True)
    if args.max_watch > 0:
        ranked = ranked[:args.max_watch]
    final_kept = dict(ranked)

    payload = {
        "event": "F2V_B_WATCH_QUALITY_HYGIENE",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "runtime_dir": str(runtime),
        "source": str(src),
        "input_watch_count": len(watch),
        "kept_count": len(final_kept),
        "dropped_count": len(dropped),
        "keep_reasons": keep_reasons.most_common(),
        "drop_reasons": drop_reasons.most_common(),
        "behavior_change": "NONE",
        "entry_gate_change": "NONE",
        "risk_change": "NONE",
        "kept": final_kept,
        "dropped": dropped,
    }
    out_state.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    state_counts = Counter(v.get("setup_state") for v in final_kept.values())
    side_counts = Counter(v.get("side") for v in final_kept.values())
    dropped_state_counts = Counter(v.get("setup_state") for v in dropped.values())

    lines = []
    lines.append("F2V_B_WATCH_QUALITY_HYGIENE_COMPACT")
    lines.append(f"generated_at={payload['generated_at']}")
    lines.append(f"runtime_dir={runtime}")
    lines.append(f"input_watch_count={len(watch)}")
    lines.append(f"kept_count={len(final_kept)}")
    lines.append(f"dropped_count={len(dropped)}")
    lines.append("")
    lines.append("KEPT_STATE_COUNTS")
    for k, v in state_counts.most_common():
        lines.append(f"{k}={v}")
    lines.append("")
    lines.append("KEPT_SIDE_COUNTS")
    for k, v in side_counts.most_common():
        lines.append(f"{k}={v}")
    lines.append("")
    lines.append("KEEP_REASONS")
    for k, v in keep_reasons.most_common():
        lines.append(f"{k}={v}")
    lines.append("")
    lines.append("DROP_REASONS")
    for k, v in drop_reasons.most_common():
        lines.append(f"{k}={v}")
    lines.append("")
    lines.append("DROPPED_STATE_COUNTS")
    for k, v in dropped_state_counts.most_common():
        lines.append(f"{k}={v}")
    lines.append("")
    lines.append("FINAL_TOP_WATCH")
    for key, item in ranked[:30]:
        lines.append(
            "|".join([
                str(item.get("setup_state")),
                str(item.get("pair")),
                str(item.get("side")),
                f"grade={item.get('shadow_grade')}",
                f"score={item.get('shadow_score')}",
                f"family_grade={item.get('family_grade')}",
                f"family_score={item.get('family_score')}",
                f"zone={item.get('pd_zone')}",
                f"direction={item.get('direction_engine')}",
                f"regime={item.get('regime_router')}",
                f"quality_reason={item.get('f2v_b_quality_reason')}",
                f"expires={item.get('expires_at')}",
            ])
        )
    lines.append("")
    lines.append("DROPPED_SAMPLE")
    for key, item in list(dropped.items())[:30]:
        lines.append(
            "|".join([
                str(item.get("setup_state")),
                str(item.get("pair")),
                str(item.get("side")),
                f"grade={item.get('shadow_grade')}",
                f"score={item.get('shadow_score')}",
                f"family_grade={item.get('family_grade')}",
                f"family_score={item.get('family_score')}",
                f"zone={item.get('pd_zone')}",
                f"direction={item.get('direction_engine')}",
                f"regime={item.get('regime_router')}",
                f"drop_reason={item.get('f2v_b_quality_reason')}",
            ])
        )
    lines.append("")
    lines.append("OUTPUT_FILES")
    lines.append(f"state={out_state}")
    lines.append(f"compact_runtime={out_compact_runtime}")
    lines.append(f"compact_root={out_compact_root}")
    lines.append("")
    lines.append("DECISION_HINT")
    lines.append("If kept list is mostly WAIT_TRIGGER, continue to trigger confirmation audit.")
    lines.append("If kept list is mostly WAIT_LOCATION, continue to location arrival/invalidation audit.")
    lines.append("If A_PLUS_MISMATCH remains, audit mismatch safety for that pair only.")
    lines.append("No entry/gate/risk behavior changed by this hygiene.")

    text = "\n".join(lines) + "\n"
    out_compact_runtime.write_text(text, encoding="utf-8")
    out_compact_root.write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
