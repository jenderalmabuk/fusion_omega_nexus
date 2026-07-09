#!/usr/bin/env python3
from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

RUNTIME = Path("user_data/revo_alpha/runtime/bybit")
HB = RUNTIME / "revo_gate_heartbeat_events.jsonl"
SH = RUNTIME / "revo_gate_shadow_events.jsonl"
OUT = Path("F2P_SHADOW_OVERRIDE_CANDIDATE_COUNTER_COMPACT.txt")
CUTOFF = datetime.fromisoformat("2026-05-08T03:34:00+00:00")

PROMOTION_GRADES = {"A", "A+"}


def parse_ts(v: Any) -> Optional[datetime]:
    if not v:
        return None
    s = str(v).replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def load_jsonl(path: Path, limit: int = 50000) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines()[-limit:]:
        try:
            e = json.loads(line)
            if not isinstance(e, dict):
                continue
            ts = parse_ts(e.get("ts"))
            if ts and ts >= CUTOFF:
                out.append(e)
        except Exception:
            pass
    return out


def clean_long_candidate(e: Dict[str, Any]) -> bool:
    return (
        str(e.get("score_vs_gate")) == "SCORE_DENY_GATE_ALLOW"
        and int(e.get("gate_allow_long", 0) or 0) == 1
        and int(e.get("final_allow_long", 0) or 0) == 0
        and str(e.get("final_reason_long")) == "DENY_SCORE_GATE_MISMATCH_SAFETY"
        and str(e.get("gate_reason_long", "")).startswith("ALLOW_")
        and str(e.get("shadow_trade_grade_long", "")).upper() in PROMOTION_GRADES
        and int(e.get("shadow_mandatory_pass_long", 0) or 0) == 1
        and str(e.get("flow_risk", "NORMAL")).upper() in {"NORMAL", "UNKNOWN"}
        and str(e.get("shadow_hard_veto_reason_long", "NONE")).upper() in {"NONE", "UNKNOWN"}
    )


def clean_short_candidate(e: Dict[str, Any]) -> bool:
    return (
        str(e.get("score_vs_gate")) == "SCORE_DENY_GATE_ALLOW"
        and int(e.get("gate_allow_short", 0) or 0) == 1
        and int(e.get("final_allow_short", 0) or 0) == 0
        and str(e.get("final_reason_short")) == "DENY_SCORE_GATE_MISMATCH_SAFETY"
        and str(e.get("gate_reason_short", "")).startswith("ALLOW_")
        and str(e.get("shadow_trade_grade_short", "")).upper() in PROMOTION_GRADES
        and int(e.get("shadow_mandatory_pass_short", 0) or 0) == 1
        and str(e.get("flow_risk", "NORMAL")).upper() in {"NORMAL", "UNKNOWN"}
        and str(e.get("shadow_hard_veto_reason_short", "NONE")).upper() in {"NONE", "UNKNOWN"}
    )


def key(e: Dict[str, Any], side: str) -> str:
    return "|".join([
        str(e.get("pair", "UNKNOWN")),
        str(e.get("candle", "UNKNOWN")),
        side,
        str(e.get("gate_reason_long" if side == "LONG" else "gate_reason_short", "")),
        str(e.get("shadow_trade_grade_long" if side == "LONG" else "shadow_trade_grade_short", "")),
    ])


def main() -> int:
    hb = load_jsonl(HB)
    sh = load_jsonl(SH)
    combined = hb + sh

    all_mismatch = [e for e in combined if str(e.get("score_vs_gate")) == "SCORE_DENY_GATE_ALLOW"]

    candidates = []
    for e in all_mismatch:
        if clean_long_candidate(e):
            candidates.append(("LONG", e))
        if clean_short_candidate(e):
            candidates.append(("SHORT", e))

    unique = {}
    for side, e in candidates:
        unique[key(e, side)] = (side, e)

    lines = []
    lines.append("F2P_SHADOW_OVERRIDE_CANDIDATE_COUNTER_COMPACT")
    lines.append(f"cutoff_utc={CUTOFF.isoformat()}")
    lines.append(f"heartbeat_events_after_cutoff={len(hb)}")
    lines.append(f"shadow_events_after_cutoff={len(sh)}")
    lines.append(f"combined_events_after_cutoff={len(combined)}")
    lines.append(f"score_deny_gate_allow_events={len(all_mismatch)}")
    lines.append(f"clean_candidate_events={len(candidates)}")
    lines.append(f"clean_candidate_unique={len(unique)}")
    lines.append("")

    lines.append("COUNTS")
    lines.append(f"mismatch_pairs={Counter(str(e.get('pair','UNKNOWN')) for e in all_mismatch).most_common(20)}")
    lines.append(f"candidate_pairs={Counter(str(e.get('pair','UNKNOWN')) for _, e in candidates).most_common(20)}")
    lines.append(f"candidate_sides={Counter(side for side, _ in candidates).most_common()}")
    lines.append("")

    lines.append("UNIQUE_CANDIDATES")
    for i, (k, (side, e)) in enumerate(unique.items(), 1):
        lines.append(f"--- candidate_{i} ---")
        lines.append(f"key={k}")
        lines.append(f"side={side}")
        for field in [
            "ts", "event", "pair", "candle", "score_vs_gate",
            "score_would_allow_long", "score_would_allow_short",
            "gate_allow_long", "gate_allow_short",
            "final_allow_long", "final_allow_short",
            "final_reason_long", "final_reason_short",
            "gate_reason_long", "gate_reason_short",
            "direction_engine", "regime_router", "pd_zone", "pd_location",
            "flow_direction", "flow_strength", "flow_authority", "flow_risk",
            "shadow_mandatory_pass_long", "shadow_mandatory_pass_short",
            "shadow_trade_grade_long", "shadow_trade_grade_short",
            "shadow_confluence_score_long", "shadow_confluence_score_short",
            "shadow_hard_veto_reason_long", "shadow_hard_veto_reason_short",
            "v139_family_grade_long", "v139_family_grade_short",
            "v139_recommended_action_long", "v139_recommended_action_short",
            "v139_hard_veto_long", "v139_hard_veto_short",
            "runtime_profile", "market_source",
        ]:
            if field in e:
                lines.append(f"{field}={e.get(field)}")
        lines.append("")

    lines.append("DECISION_HINT")
    if len(unique) == 0:
        lines.append("No clean override candidate. Keep safety deny.")
    elif len(unique) < 3:
        lines.append("Clean candidate exists but sample is too small. Continue observation; no behavior patch yet.")
    else:
        lines.append("Multiple clean candidates found. Consider env-switch paper-only arbitration shadow patch proposal.")
    lines.append("Do not change entry/gate globally from this report alone.")

    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(OUT.read_text())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
