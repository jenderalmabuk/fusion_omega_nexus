"""Forensic replay of clean Fusion Quantum losses on real 1m mainnet candles."""
from __future__ import annotations

import json
from pathlib import Path

import httpx
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
JOURNAL = ROOT / "runtime/fusion_quantum/journal/trade_history.json"
CUTOFF = pd.Timestamp("2026-07-26T12:01:33Z")
API = "http://127.0.0.1:8000"


def candles(symbol: str, exchange: str) -> pd.DataFrame:
    r = httpx.get(f"{API}/klines/{exchange}/{symbol}", params={"tf": "1m", "limit": 2000}, timeout=30)
    r.raise_for_status()
    rows = r.json().get("data", [])
    df = pd.DataFrame(rows)
    if not df.empty:
        df["open_time"] = pd.to_datetime(df["open_time"], utc=True)
        for col in ("open", "high", "low", "close"):
            df[col] = pd.to_numeric(df[col])
    return df.sort_values("open_time")


def first_hits(trade: dict, df: pd.DataFrame) -> dict:
    opened, closed = pd.Timestamp(trade["timestamp_open"]), pd.Timestamp(trade["timestamp_close"])
    q = df[(df.open_time >= opened.floor("min")) & (df.open_time <= closed.ceil("min"))].copy()
    sl = float(trade["sl_original"])
    entry = float(trade["entry_price"])
    risk = abs(entry - sl)
    tp = entry + 2 * risk if trade["side"] == "LONG" else entry - 2 * risk
    if trade["side"] == "LONG":
        sl_rows, tp_rows = q[q.low <= sl], q[q.high >= tp]
    else:
        sl_rows, tp_rows = q[q.high >= sl], q[q.low <= tp]
    sl_at = sl_rows.open_time.iloc[0] if len(sl_rows) else None
    tp_at = tp_rows.open_time.iloc[0] if len(tp_rows) else None
    if sl_at is not None and tp_at is not None:
        order = "same_1m_ambiguous" if sl_at == tp_at else ("tp_first" if tp_at < sl_at else "sl_first")
    elif sl_at is not None:
        order = "sl_only"
    elif tp_at is not None:
        order = "tp_only"
    else:
        order = "neither"
    return {
        "bars": len(q), "entry": entry, "sl": sl, "tp_2r": tp,
        "sl_at": str(sl_at) if sl_at is not None else None,
        "tp_at": str(tp_at) if tp_at is not None else None,
        "order": order,
        "min_low": float(q.low.min()) if len(q) else None,
        "max_high": float(q.high.max()) if len(q) else None,
    }


def main() -> None:
    trades = [x for x in json.loads(JOURNAL.read_text())
              if pd.Timestamp(x["timestamp_open"]) >= CUTOFF and x["pnl_usd"] < 0]
    report = []
    for trade in trades:
        row = {"symbol": trade["symbol"], "side": trade["side"],
               "opened": trade["timestamp_open"], "closed": trade["timestamp_close"],
               "pnl_usd": trade["pnl_usd"], "hold_minutes": trade["hold_minutes"]}
        for exchange in ("binance", "bybit"):
            try:
                row[exchange] = first_hits(trade, candles(trade["symbol"], exchange))
            except Exception as exc:
                row[exchange] = {"error": repr(exc)}
        report.append(row)
        print(trade["symbol"], trade["side"],
              "binance", row["binance"].get("order"), row["binance"].get("sl_at"), row["binance"].get("tp_at"),
              "bybit", row["bybit"].get("order"), row["bybit"].get("sl_at"), row["bybit"].get("tp_at"), flush=True)
    dest = ROOT / "fusion_quantum/results/clean_loss_1m_forensics.json"
    dest.write_text(json.dumps(report, indent=2))
    print("written", dest)


if __name__ == "__main__":
    main()
