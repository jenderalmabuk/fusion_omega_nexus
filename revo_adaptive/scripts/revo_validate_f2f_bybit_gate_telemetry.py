#!/usr/bin/env python3
import argparse
import json
import time
from collections import Counter
from pathlib import Path

def read_tail_jsonl(path: Path, max_lines: int = 500):
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()[-max_lines:]
    out = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runtime-dir", default="/home/fusion_omega/revo_adaptive/user_data/revo_alpha/runtime/bybit")
    ap.add_argument("--max-age-sec", type=float, default=900)
    args = ap.parse_args()

    runtime = Path(args.runtime_dir)
    hb = runtime / "revo_gate_heartbeat_events.jsonl"
    sh = runtime / "revo_gate_shadow_events.jsonl"

    print("F2F_BYBIT_GATE_TELEMETRY_AUDIT")
    print("runtime=", runtime)

    failures = []

    for p in [hb, sh]:
        print()
        print("---", p.name, "---")
        if not p.exists():
            print("exists=False")
            failures.append(f"MISSING:{p.name}")
            continue

        age = time.time() - p.stat().st_mtime
        print("exists=True age_sec=", round(age, 1), "size=", p.stat().st_size)
        if age > args.max_age_sec:
            failures.append(f"STALE:{p.name}:{age:.1f}")

        events = read_tail_jsonl(p, 800)
        print("tail_events=", len(events))
        if not events:
            failures.append(f"NO_JSON_EVENTS:{p.name}")
            continue

        final_allow_long = sum(int(e.get("final_allow_long", 0) or 0) for e in events)
        final_allow_short = sum(int(e.get("final_allow_short", 0) or 0) for e in events)

        reasons = Counter()
        score_vs_gate = Counter()
        pairs = Counter()

        for e in events:
            pairs[str(e.get("pair", "UNKNOWN"))] += 1
            score_vs_gate[str(e.get("score_vs_gate", "UNKNOWN"))] += 1
            for k in ["final_reason_long", "final_reason_short", "gate_reason_long", "gate_reason_short"]:
                v = str(e.get(k, "") or "")
                if v:
                    reasons[v] += 1

        print("final_allow_long_sum=", final_allow_long)
        print("final_allow_short_sum=", final_allow_short)
        print("top_score_vs_gate=", score_vs_gate.most_common(10))
        print("top_reasons=", reasons.most_common(20))
        print("top_pairs=", pairs.most_common(15))

        sample = events[-1]
        required_any = [
            "pair",
            "final_allow_long",
            "final_allow_short",
            "gate_reason_long",
            "gate_reason_short",
            "final_reason_long",
            "final_reason_short",
        ]
        missing = [k for k in required_any if k not in sample]
        print("latest_event_required_missing=", missing)
        if missing:
            failures.append(f"MISSING_KEYS:{p.name}:{','.join(missing)}")

    if failures:
        print()
        print("failures=", len(failures))
        for f in failures:
            print("FAIL:", f)
        print("F2F_BYBIT_GATE_TELEMETRY_FAIL")
        return 1

    print()
    print("failures=0")
    print("F2F_BYBIT_GATE_TELEMETRY_PASS")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
