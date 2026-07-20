#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from auditable_core.core import Candle, append_jsonl, replay_candidates


def load_csv(path: Path, limit: int) -> list[Candle]:
    rows: list[Candle] = []
    with path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            try:
                close = float(row["close"])
                volume = float(row["volume"])
                ema55 = float(row.get("ema55") or row.get("ema_55") or close)
                rsi = float(row.get("rsi") or row.get("rsi_14") or 50)
                atr_pct = float(row.get("atr_pct") or 2)
            except (KeyError, ValueError):
                continue
            rows.append(Candle(close=close, volume=volume, rsi=rsi, ema55=ema55, atr_pct=atr_pct))
            if len(rows) >= limit:
                break
    return rows


def synthetic() -> list[Candle]:
    return [
        Candle(close=100, volume=500, rsi=55, ema55=101, atr_pct=2),
        Candle(close=96, volume=3_000, rsi=39, ema55=100, atr_pct=2),
        Candle(close=89, volume=4_000, rsi=25, ema55=100, atr_pct=9),
    ]


def main() -> int:
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    out = Path(sys.argv[2]) if len(sys.argv) > 2 else ROOT / "user_data/local/auditable_core_sample.jsonl"
    candles = load_csv(src, 500) if src and src.exists() else synthetic()
    decisions = list(replay_candidates("SAMPLE/USDT:USDT", candles, flow="long", btc_mode="neutral"))
    for idx, decision in enumerate(decisions):
        append_jsonl(out, {"event": "sample_gate_decision", "idx": idx, "allow": decision.allow, "grade": decision.grade, "score": decision.score, "reasons": list(decision.reasons)})
    allowed = sum(1 for d in decisions if d.allow)
    print(json.dumps({"candles": len(candles), "allowed": allowed, "denied": len(decisions) - allowed, "journal": str(out)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
