"""Historical Binance USDⓈ-M klines fetcher with on-disk cache (public API, no auth)."""
from __future__ import annotations

import time
from pathlib import Path

import pandas as pd
import requests

_BASE = "https://fapi.binance.com/fapi/v1/klines"
_CACHE = Path(__file__).parent / "cache"
_MS = {"1m": 60_000, "3m": 180_000, "5m": 300_000, "15m": 900_000, "30m": 1_800_000, "1h": 3_600_000, "4h": 14_400_000}


def fetch_klines(symbol: str, interval: str = "15m", days: int = 120) -> pd.DataFrame:
    """Return OHLCV DataFrame [open_time, open, high, low, close, volume]. Cached per (symbol,interval,days)."""
    _CACHE.mkdir(parents=True, exist_ok=True)
    cache_file = _CACHE / f"{symbol}_{interval}_{days}d.csv"
    if cache_file.exists():
        cached = pd.read_csv(cache_file, parse_dates=["open_time"])
        if "taker_buy_base" in cached.columns:   # else stale schema -> refetch below
            return cached

    step = _MS[interval]
    end = int(time.time() * 1000)
    start = end - days * 86_400_000
    rows: list[list] = []
    cur = start
    while cur < end:
        r = requests.get(_BASE, params={"symbol": symbol, "interval": interval,
                                        "startTime": cur, "limit": 1500}, timeout=20)
        r.raise_for_status()
        batch = r.json()
        if not batch:
            break
        rows.extend(batch)
        cur = batch[-1][0] + step
        if len(batch) < 1500:
            break
        time.sleep(0.25)  # be gentle with the public endpoint

    df = pd.DataFrame(rows, columns=["open_time", "open", "high", "low", "close", "volume",
                                     "close_time", "qav", "trades", "tbav", "tqav", "ignore"])
    df = df[["open_time", "open", "high", "low", "close", "volume", "tbav"]].astype(float)
    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms")
    df = df.rename(columns={"tbav": "taker_buy_base"})  # CVD source (taker buy base volume)
    df = df.drop_duplicates("open_time").reset_index(drop=True)
    df.to_csv(cache_file, index=False)
    return df


def fetch_recent(symbol: str, interval: str = "1h", limit: int = 300) -> pd.DataFrame:
    """Latest `limit` klines, UNCACHED — for live forward polling. Includes the forming candle."""
    last_exc = None
    for attempt in range(4):
        try:
            r = requests.get(_BASE, params={"symbol": symbol, "interval": interval, "limit": limit}, timeout=20)
            r.raise_for_status()
            break
        except Exception as exc:  # transient (connection reset / timeout) -> retry with backoff
            last_exc = exc
            if attempt == 3:
                raise
            time.sleep(0.5 * (attempt + 1))
    df = pd.DataFrame(r.json(), columns=["open_time", "open", "high", "low", "close", "volume",
                                         "close_time", "qav", "trades", "tbav", "tqav", "ignore"])
    df = df[["open_time", "open", "high", "low", "close", "volume", "tbav"]].astype(float)
    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms")
    df = df.rename(columns={"tbav": "taker_buy_base"})
    return df.drop_duplicates("open_time").reset_index(drop=True)


def fetch_funding(symbol: str, days: int = 365) -> pd.DataFrame:
    """Historical funding rates -> DataFrame[fundingTime, funding_rate] (fraction per 8h)."""
    end = int(time.time() * 1000)
    cur = end - days * 86_400_000
    rows: list[dict] = []
    while cur < end:
        r = requests.get("https://fapi.binance.com/fapi/v1/fundingRate",
                         params={"symbol": symbol, "startTime": cur, "limit": 1000}, timeout=20)
        r.raise_for_status()
        batch = r.json()
        if not batch:
            break
        rows.extend(batch)
        cur = batch[-1]["fundingTime"] + 1
        if len(batch) < 1000:
            break
        time.sleep(0.2)
    if not rows:
        return pd.DataFrame(columns=["fundingTime", "funding_rate"])
    df = pd.DataFrame(rows)
    df["fundingTime"] = pd.to_datetime(df["fundingTime"], unit="ms")
    df["funding_rate"] = df["fundingRate"].astype(float)
    return df[["fundingTime", "funding_rate"]].drop_duplicates("fundingTime").reset_index(drop=True)


def fetch_oi(symbol: str, period: str = "1h", limit: int = 500) -> pd.DataFrame:
    """Open-interest history -> DataFrame[oi_time, oi]. Binance keeps only ~last 30 days."""
    r = requests.get("https://fapi.binance.com/futures/data/openInterestHist",
                     params={"symbol": symbol, "period": period, "limit": limit}, timeout=20)
    r.raise_for_status()
    data = r.json()
    if not data:
        return pd.DataFrame(columns=["oi_time", "oi"])
    df = pd.DataFrame(data)
    df["oi_time"] = pd.to_datetime(df["timestamp"], unit="ms")
    df["oi"] = df["sumOpenInterest"].astype(float)
    return df[["oi_time", "oi"]].drop_duplicates("oi_time").reset_index(drop=True)
