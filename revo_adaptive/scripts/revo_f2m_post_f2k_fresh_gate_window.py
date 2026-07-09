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
PAIRLIST = RUNTIME / "pair_universe_remote.json"
EXEC = RUNTIME / "revo_execution_context.json"
OUT = Path("F2M_POST_F2K_FRESH_GATE_WINDOW_COMPACT.txt")

# F2K-C paper whitelist confirmed around 2026-05-08 03:34:03 UTC.
CUTOFF = datetime.fromisoformat("2026-05-08T03:34:00+00:00")


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


def load_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def load_jsonl_after(path: Path, cutoff: datetime, limit: int = 20000) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    out = []
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()[-limit:]
    for line in lines:
        try:
            e = json.loads(line)
            if not isinstance(e, dict):
                continue
            ts = parse_ts(e.get("ts"))
            if ts and ts >= cutoff:
                out.append(e)
        except Exception:
            pass
    return out


def reason_counts(events: List[Dict[str, Any]]) -> Counter:
    c = Counter()
    for e in events:
        for k in ["final_reason_long", "final_reason_short", "gate_reason_long", "gate_reason_short", "context_deny_reason"]:
            v = str(e.get(k, "") or "")
            if v:
                c[v] += 1
    return c


def count_by(events: List[Dict[str, Any]], key: str) -> Counter:
    return Counter(str(e.get(key, "UNKNOWN")) for e in events)


def final_allow_count(events: List[Dict[str, Any]]) -> int:
    total = 0
    for e in events:
        total += int(e.get("final_allow_long", 0) or 0)
        total += int(e.get("final_allow_short", 0) or 0)
    return total


def main() -> int:
    hb = load_jsonl_after(HB, CUTOFF)
    sh = load_jsonl_after(SH, CUTOFF)
    combined = hb + sh

    pairlist = load_json(PAIRLIST)
    pairs = pairlist.get("pairs", [])
    if not isinstance(pairs, list):
        pairs = []

    exec_data = load_json(EXEC)
    exec_pairs = exec_data.get("pairs", {})
    if not isinstance(exec_pairs, dict):
        exec_pairs = {}

    lines = []
    lines.append("F2M_POST_F2K_FRESH_GATE_WINDOW_COMPACT")
    lines.append(f"cutoff_utc={CUTOFF.isoformat()}")
    lines.append(f"heartbeat_events_after_cutoff={len(hb)}")
    lines.append(f"shadow_events_after_cutoff={len(sh)}")
    lines.append(f"combined_events_after_cutoff={len(combined)}")
    lines.append("")

    lines.append("CURRENT_PAIRLIST")
    lines.append(f"pair_count={len(pairs)}")
    lines.append(f"f2k_enabled={pairlist.get('f2k_sticky_hygiene_enabled')}")
    lines.append(f"f2k_drop_count={pairlist.get('f2k_drop_count')}")
    lines.append(f"pairs={pairs}")
    lines.append("")

    lines.append("CURRENT_EXECUTION_CONTEXT")
    lines.append(f"contract_status={exec_data.get('contract_status')}")
    lines.append(f"remote_pair_count={exec_data.get('remote_pair_count')}")
    lines.append(f"execution_pair_count={exec_data.get('execution_pair_count')}")
    lines.append(f"exec_authority={Counter(str(r.get('flow_authority','UNKNOWN')) for r in exec_pairs.values() if isinstance(r, dict)).most_common(20)}")
    lines.append(f"exec_entry_permission={Counter(str(r.get('entry_permission','UNKNOWN')) for r in exec_pairs.values() if isinstance(r, dict)).most_common(20)}")
    lines.append(f"exec_direction={Counter(str(r.get('flow_direction','UNKNOWN')) for r in exec_pairs.values() if isinstance(r, dict)).most_common(20)}")
    lines.append("")

    lines.append("POST_F2K_GATE_SUMMARY")
    lines.append(f"heartbeat_final_allow_total={final_allow_count(hb)}")
    lines.append(f"shadow_final_allow_total={final_allow_count(sh)}")
    lines.append(f"combined_final_allow_total={final_allow_count(combined)}")
    lines.append(f"heartbeat_score_vs_gate={count_by(hb, 'score_vs_gate').most_common(20)}")
    lines.append(f"shadow_score_vs_gate={count_by(sh, 'score_vs_gate').most_common(20)}")
    lines.append(f"combined_score_vs_gate={count_by(combined, 'score_vs_gate').most_common(20)}")
    lines.append("")

    lines.append("POST_F2K_REASONS_HEARTBEAT")
    for k, v in reason_counts(hb).most_common(30):
        lines.append(f"{k}={v}")
    lines.append("")

    lines.append("POST_F2K_REASONS_SHADOW")
    for k, v in reason_counts(sh).most_common(30):
        lines.append(f"{k}={v}")
    lines.append("")

    lines.append("POST_F2K_PAIR_BREAKDOWN")
    pair_c = Counter(str(e.get("pair", "UNKNOWN")) for e in combined)
    for pair, n in pair_c.most_common(30):
        evs = [e for e in combined if str(e.get("pair", "UNKNOWN")) == pair]
        rc = reason_counts(evs).most_common(5)
        sg = count_by(evs, "score_vs_gate").most_common(5)
        lines.append(f"{pair} events={n} score={sg} reasons={rc}")
    lines.append("")

    lines.append("POST_F2K_ACTIVE_PAIR_DETAIL")
    for pair in pairs:
        evs = [e for e in combined if str(e.get("pair", "")) == pair]
        lines.append(
            f"{pair} events={len(evs)} "
            f"score={count_by(evs, 'score_vs_gate').most_common(5)} "
            f"reasons={reason_counts(evs).most_common(5)}"
        )
    lines.append("")

    lines.append("DECISION_HINT")
    lines.append("If DENY_STICKY_RETAINED_CURRENT_FLOW_NOT_ACTIONABLE falls sharply after cutoff, F2K-C fixed pairlist noise.")
    lines.append("If final_allow remains 0 and reasons are premium/trap/direction/timing/chop, gate deny is valid market quality filter.")
    lines.append("If SCORE_ALLOW_GATE_DENY persists on same pairs, audit those pairs only; do not loosen global gate.")
    lines.append("No entry/gate patch from this audit alone.")

    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(OUT.read_text())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
