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
OUT = Path("F2Q_EXTENDED_CANDIDATE_OBSERVATION_COMPACT.txt")
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

def load_jsonl(path: Path, limit: int = 80000) -> List[Dict[str, Any]]:
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

def is_clean_candidate(e: Dict[str, Any], side: str) -> bool:
    s = side.lower()
    return (
        str(e.get("score_vs_gate")) == "SCORE_DENY_GATE_ALLOW"
        and int(e.get(f"gate_allow_{s}", 0) or 0) == 1
        and int(e.get(f"final_allow_{s}", 0) or 0) == 0
        and str(e.get(f"final_reason_{s}")) == "DENY_SCORE_GATE_MISMATCH_SAFETY"
        and str(e.get(f"gate_reason_{s}", "")).startswith("ALLOW_")
        and str(e.get(f"shadow_trade_grade_{s}", "")).upper() in PROMOTION_GRADES
        and int(e.get(f"shadow_mandatory_pass_{s}", 0) or 0) == 1
        and str(e.get("flow_risk", "NORMAL")).upper() in {"NORMAL", "UNKNOWN"}
        and str(e.get(f"shadow_hard_veto_reason_{s}", "NONE")).upper() in {"NONE", "UNKNOWN"}
    )

def unique_key(e: Dict[str, Any], side: str) -> str:
    return "|".join([
        str(e.get("pair", "UNKNOWN")),
        str(e.get("candle", "UNKNOWN")),
        side,
        str(e.get(f"gate_reason_{side.lower()}", "")),
        str(e.get(f"shadow_trade_grade_{side.lower()}", "")),
    ])

def main() -> int:
    hb = load_jsonl(HB)
    sh = load_jsonl(SH)
    combined = hb + sh

    clean = []
    for e in combined:
        if is_clean_candidate(e, "LONG"):
            clean.append(("LONG", e))
        if is_clean_candidate(e, "SHORT"):
            clean.append(("SHORT", e))

    unique = {}
    for side, e in clean:
        unique[unique_key(e, side)] = (side, e)

    lines = []
    lines.append("F2Q_EXTENDED_CANDIDATE_OBSERVATION_COMPACT")
    lines.append(f"cutoff_utc={CUTOFF.isoformat()}")
    lines.append(f"heartbeat_events_after_cutoff={len(hb)}")
    lines.append(f"shadow_events_after_cutoff={len(sh)}")
    lines.append(f"combined_events_after_cutoff={len(combined)}")
    lines.append(f"clean_candidate_events={len(clean)}")
    lines.append(f"clean_candidate_unique={len(unique)}")
    lines.append(f"unique_pairs={len(set(str(e.get('pair','UNKNOWN')) for _, e in unique.values()))}")
    lines.append("")
    lines.append("COUNTS")
    lines.append(f"candidate_pairs={Counter(str(e.get('pair','UNKNOWN')) for _, e in clean).most_common(30)}")
    lines.append(f"candidate_sides={Counter(side for side, _ in clean).most_common()}")
    lines.append(f"candidate_gate_reasons_long={Counter(str(e.get('gate_reason_long','')) for side, e in clean if side == 'LONG').most_common(20)}")
    lines.append(f"candidate_gate_reasons_short={Counter(str(e.get('gate_reason_short','')) for side, e in clean if side == 'SHORT').most_common(20)}")
    lines.append("")
    lines.append("UNIQUE_CANDIDATES")
    for i, (k, (side, e)) in enumerate(unique.items(), 1):
        lines.append(f"{i}. {k} ts={e.get('ts')} pd_zone={e.get('pd_zone')} direction={e.get('direction_engine')} shadow_grade={e.get('shadow_trade_grade_' + side.lower())} score={e.get('shadow_confluence_score_' + side.lower())}")
    lines.append("")
    lines.append("DECISION")
    if len(unique) < 3:
        lines.append("HOLD_BEHAVIOR_PATCH: sample masih terlalu kecil.")
    elif len(unique) < 6:
        lines.append("CONTINUE_OBSERVATION: ada pola awal, belum cukup untuk override.")
    else:
        lines.append("READY_FOR_PATCH_PROPOSAL: boleh susun env-switch paper-only override proposal, belum auto-approve.")
    lines.append("No entry/gate behavior changed by this audit.")

    OUT.write_text("\\n".join(lines) + "\\n", encoding="utf-8")
    print(OUT.read_text())

if __name__ == "__main__":
    main()
