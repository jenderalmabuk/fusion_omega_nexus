#!/usr/bin/env python3
from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List

RUNTIME = Path("user_data/revo_alpha/runtime/bybit")
HB = RUNTIME / "revo_gate_heartbeat_events.jsonl"
SH = RUNTIME / "revo_gate_shadow_events.jsonl"
OUT = Path("F2H_BYBIT_GATE_BLOCKER_ATTRIBUTION_COMPACT.txt")


def load_jsonl(path: Path, limit: int = 5000) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()[-limit:]
    out: List[Dict[str, Any]] = []
    for line in lines:
        try:
            obj = json.loads(line)
            if isinstance(obj, dict):
                out.append(obj)
        except Exception:
            pass
    return out


def add_reason_counts(events: Iterable[Dict[str, Any]]) -> Counter:
    c = Counter()
    for e in events:
        for k in ("final_reason_long", "final_reason_short", "gate_reason_long", "gate_reason_short", "context_deny_reason"):
            v = str(e.get(k, "") or "")
            if v:
                c[v] += 1
    return c


def count_by(events: Iterable[Dict[str, Any]], key: str) -> Counter:
    c = Counter()
    for e in events:
        c[str(e.get(key, "UNKNOWN"))] += 1
    return c


def pair_score_gate_mismatch(events: Iterable[Dict[str, Any]]) -> Counter:
    c = Counter()
    for e in events:
        if str(e.get("score_vs_gate", "")) == "SCORE_ALLOW_GATE_DENY":
            c[str(e.get("pair", "UNKNOWN"))] += 1
    return c


def pair_final_allow(events: Iterable[Dict[str, Any]]) -> Counter:
    c = Counter()
    for e in events:
        if int(e.get("final_allow_long", 0) or 0) or int(e.get("final_allow_short", 0) or 0):
            c[str(e.get("pair", "UNKNOWN"))] += 1
    return c


def family_breakdown(events: Iterable[Dict[str, Any]]) -> Counter:
    c = Counter()
    for e in events:
        fam = str(e.get("v139_entry_family", "UNKNOWN"))
        prof = str(e.get("v139_family_profile", "UNKNOWN"))
        c[f"{fam}|{prof}"] += 1
    return c


def deny_bucket(reason: str) -> str:
    r = reason.upper()
    if "CONTEXT" in r:
        return "CONTEXT_CONTRACT"
    if "STICKY" in r:
        return "STICKY_NOT_ACTIONABLE"
    if "TRAP" in r:
        return "FLOW_TRAP_RISK"
    if "FLOW_DIRECTION" in r:
        return "FLOW_DIRECTION"
    if "PREMIUM" in r or "DISCOUNT" in r or "RANGING_MID" in r:
        return "LOCATION_PD_ZONE"
    if "TIMING" in r:
        return "TIMING"
    if "CHOP" in r:
        return "CHOP"
    if "TPSL" in r or "GEOMETRY" in r:
        return "GEOMETRY_TPSL"
    return "OTHER"


def bucket_counts(reason_counts: Counter) -> Counter:
    out = Counter()
    for reason, n in reason_counts.items():
        out[deny_bucket(reason)] += n
    return out


def top_pair_reason(events: Iterable[Dict[str, Any]], topn: int = 30) -> List[str]:
    pc: Dict[str, Counter] = defaultdict(Counter)
    for e in events:
        pair = str(e.get("pair", "UNKNOWN"))
        for k in ("final_reason_long", "final_reason_short", "gate_reason_long", "gate_reason_short"):
            v = str(e.get(k, "") or "")
            if v:
                pc[pair][v] += 1

    rows = []
    for pair, c in pc.items():
        total = sum(c.values())
        main, n = c.most_common(1)[0] if c else ("NONE", 0)
        rows.append((total, pair, main, n, c.most_common(5)))

    rows.sort(reverse=True)
    return [f"{pair} total={total} top={main}:{n} reasons={reasons}" for total, pair, main, n, reasons in rows[:topn]]


def main() -> int:
    hb = load_jsonl(HB, 5000)
    sh = load_jsonl(SH, 5000)
    combined = hb + sh

    hb_reasons = add_reason_counts(hb)
    sh_reasons = add_reason_counts(sh)
    all_reasons = add_reason_counts(combined)

    lines = []
    lines.append("F2H_BYBIT_GATE_BLOCKER_ATTRIBUTION_COMPACT")
    lines.append(f"heartbeat_events={len(hb)}")
    lines.append(f"shadow_events={len(sh)}")
    lines.append(f"combined_events={len(combined)}")
    lines.append("")
    lines.append("FINAL_ALLOW")
    lines.append(f"heartbeat_final_allow_pairs={pair_final_allow(hb).most_common(30)}")
    lines.append(f"shadow_final_allow_pairs={pair_final_allow(sh).most_common(30)}")
    lines.append("")
    lines.append("SCORE_VS_GATE")
    lines.append(f"heartbeat_score_vs_gate={count_by(hb, 'score_vs_gate').most_common(20)}")
    lines.append(f"shadow_score_vs_gate={count_by(sh, 'score_vs_gate').most_common(20)}")
    lines.append("")
    lines.append("TOP_REASON_HEARTBEAT")
    for k, v in hb_reasons.most_common(30):
        lines.append(f"{k}={v}")
    lines.append("")
    lines.append("TOP_REASON_SHADOW")
    for k, v in sh_reasons.most_common(30):
        lines.append(f"{k}={v}")
    lines.append("")
    lines.append("DENY_BUCKETS_COMBINED")
    for k, v in bucket_counts(all_reasons).most_common(20):
        lines.append(f"{k}={v}")
    lines.append("")
    lines.append("SCORE_ALLOW_GATE_DENY_PAIRS_HEARTBEAT")
    for k, v in pair_score_gate_mismatch(hb).most_common(30):
        lines.append(f"{k}={v}")
    lines.append("")
    lines.append("SCORE_ALLOW_GATE_DENY_PAIRS_SHADOW")
    for k, v in pair_score_gate_mismatch(sh).most_common(30):
        lines.append(f"{k}={v}")
    lines.append("")
    lines.append("ENTRY_FAMILY_BREAKDOWN")
    for k, v in family_breakdown(combined).most_common(30):
        lines.append(f"{k}={v}")
    lines.append("")
    lines.append("TOP_PAIR_REASON")
    lines.extend(top_pair_reason(combined, 30))
    lines.append("")
    lines.append("DECISION_HINT")
    lines.append("If top buckets are CONTEXT_CONTRACT/STICKY_NOT_ACTIONABLE, investigate context lookup/sticky TTL before entry patch.")
    lines.append("If top buckets are FLOW_TRAP_RISK/FLOW_DIRECTION/LOCATION_PD_ZONE/TIMING, denials are likely valid market/gate filters.")
    lines.append("Do not loosen entry from this report alone.")

    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(OUT.read_text())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
