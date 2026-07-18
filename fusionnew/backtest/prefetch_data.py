"""Pre-fetch semua kline yang dibutuhkan sweep, dengan backoff tahan-429.
Sekali jalan → sisi sweep murni baca cache (no network). Aman diulang (skip yang ada).
"""
from __future__ import annotations
import sys, time, os
from pathlib import Path
import pandas as pd
import requests

CACHE = Path("/home/fusion_omega/fusion_omega_nexus/fusionnew/backtest/cache")
BASE = "https://fapi.binance.com/fapi/v1/klines"
MS = {"1m": 60_000, "3m": 180_000, "5m": 300_000, "15m": 900_000,
      "30m": 1_800_000, "1h": 3_600_000, "4h": 14_400_000}

PAIRS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "AVAXUSDT", "INJUSDT",
         "DOGEUSDT", "WLDUSDT", "XLMUSDT", "LINKUSDT", "NEARUSDT", "DOTUSDT"]
# tier -> (zone, ltf): H1(1h,5m) M30(30m,5m) M15(15m,3m)
INTERVALS = ["1h", "5m", "30m", "15m", "3m"]
DAYS = 120


def _get(params, tries=8):
    """GET dgn backoff eksponensial pada 429/5xx."""
    delay = 2.0
    for attempt in range(tries):
        r = requests.get(BASE, params=params, timeout=25)
        if r.status_code == 429 or r.status_code >= 500:
            wait = delay * (2 ** attempt)
            ra = r.headers.get("Retry-After")
            if ra:
                try: wait = max(wait, float(ra))
                except: pass
            print(f"    429/5xx -> sleep {wait:.0f}s (attempt {attempt+1})", flush=True)
            time.sleep(min(wait, 60))
            continue
        r.raise_for_status()
        return r.json()
    raise RuntimeError("max retries exceeded")


def fetch(symbol, interval, days):
    CACHE.mkdir(parents=True, exist_ok=True)
    cf = CACHE / f"{symbol}_{interval}_{days}d.csv"
    if cf.exists():
        c = pd.read_csv(cf, nrows=1)
        if "taker_buy_base" in c.columns:
            return "cached"
    step = MS[interval]
    end = int(time.time() * 1000)
    start = end - days * 86_400_000
    rows, cur = [], start
    while cur < end:
        batch = _get({"symbol": symbol, "interval": interval, "startTime": cur, "limit": 1500})
        if not batch:
            break
        rows.extend(batch)
        cur = batch[-1][0] + step
        if len(batch) < 1500:
            break
        time.sleep(0.6)  # gentle
    if not rows:
        return "empty"
    df = pd.DataFrame(rows, columns=["open_time", "open", "high", "low", "close", "volume",
                                     "close_time", "qav", "trades", "tbav", "tqav", "ignore"])
    df = df[["open_time", "open", "high", "low", "close", "volume", "tbav"]].astype(float)
    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms")
    df = df.rename(columns={"tbav": "taker_buy_base"})
    df.to_csv(cf, index=False)
    return f"{len(df)} rows"


if __name__ == "__main__":
    tot = len(PAIRS) * len(INTERVALS)
    done = 0
    t0 = time.time()
    for iv in INTERVALS:
        for s in PAIRS:
            done += 1
            try:
                res = fetch(s, iv, DAYS)
                print(f"  [{done}/{tot}] {s} {iv}: {res}", flush=True)
            except Exception as e:
                print(f"  [{done}/{tot}] {s} {iv}: ERR {e}", flush=True)
            time.sleep(0.3)
    print(f"DONE prefetch in {time.time()-t0:.0f}s", flush=True)
