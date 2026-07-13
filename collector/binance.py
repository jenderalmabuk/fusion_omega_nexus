"""Binance USDT-M futures collector: klines + OI + funding.

Continuous loop (never one-shot). Only CLOSED candles are stored: the last
(kline is forming) row from the REST response is dropped. All writes are
UPSERTs so exchange-revised candles get corrected.
"""
from __future__ import annotations

import asyncio
import os
import time
from datetime import datetime, timezone

import httpx

from collector.db import (last_oi, upsert_funding, upsert_klines, upsert_oi,
                          upsert_universe)
from collector.universe import load_universe

BASE = os.getenv("BINANCE_FAPI_BASE", "https://fapi.binance.com")

# Engine-critical TFs: 1m (manage), 3m/5m/15m (LTF), 30m/1h (zone TIERS), 4h (H4)
KLINE_TFS = ["1m", "3m", "5m", "15m", "30m", "1h", "4h"]
TF_SEC = {"1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800,
          "1h": 3600, "4h": 14400, "1d": 86400}
KLINE_LIMIT = int(os.getenv("KLINE_LIMIT", "500"))
LOOP_SEC = int(os.getenv("COLLECTOR_LOOP_SEC", "60"))
OI_LOOP_SEC = int(os.getenv("OI_LOOP_SEC", "300"))
FUNDING_LOOP_SEC = int(os.getenv("FUNDING_LOOP_SEC", "900"))
CONCURRENCY = int(os.getenv("COLLECTOR_CONCURRENCY", "4"))


def _ts(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)


def _kline_rows(symbol: str, tf: str, data: list[list]) -> list[tuple]:
    """Convert raw klines to rows, DROPPING the still-forming last candle."""
    now_ms = time.time() * 1000
    rows = []
    for k in data:
        close_ms = float(k[6])
        if close_ms > now_ms:          # candle not closed yet — skip
            continue
        rows.append((
            "binance", symbol, tf, _ts(k[0]),
            float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[5]),
            _ts(k[6]), float(k[7]), int(k[8]), float(k[9]), float(k[10]),
        ))
    return rows


async def _collect_klines(client: httpx.AsyncClient, sem: asyncio.Semaphore,
                          symbol: str, tf: str) -> None:
    async with sem:
        try:
            r = await client.get(f"{BASE}/fapi/v1/klines",
                                 params={"symbol": symbol, "interval": tf, "limit": KLINE_LIMIT})
            r.raise_for_status()
            await upsert_klines(_kline_rows(symbol, tf, r.json()))
        except Exception as exc:
            print(f"[binance] klines ERR {symbol} {tf}: {type(exc).__name__} {str(exc)[:80]}", flush=True)


async def _collect_oi(client: httpx.AsyncClient, sem: asyncio.Semaphore, symbol: str) -> None:
    async with sem:
        try:
            r = await client.get(f"{BASE}/futures/data/openInterestHist",
                                 params={"symbol": symbol, "period": "5m", "limit": 30})
            r.raise_for_status()
            data = r.json()
            prev = await last_oi("binance", symbol, "5m")
            rows = []
            for d in data:
                oi = float(d["sumOpenInterestValue"])
                delta = oi - prev if prev is not None else 0.0
                pct = (delta / prev * 100) if prev else 0.0
                rows.append(("binance", symbol, "5m", _ts(int(d["timestamp"])), oi, delta, pct))
                prev = oi
            await upsert_oi(rows)
        except Exception as exc:
            print(f"[binance] oi ERR {symbol}: {type(exc).__name__} {str(exc)[:80]}", flush=True)


async def _collect_funding(client: httpx.AsyncClient, sem: asyncio.Semaphore, symbol: str) -> None:
    async with sem:
        try:
            r = await client.get(f"{BASE}/fapi/v1/fundingRate",
                                 params={"symbol": symbol, "limit": 30})
            r.raise_for_status()
            data = r.json()
            rates = [float(d["fundingRate"]) for d in data]
            rows = []
            for i, d in enumerate(data):
                window = rates[max(0, i - 23):i + 1]
                mean = sum(window) / len(window)
                var = sum((x - mean) ** 2 for x in window) / len(window)
                z = (rates[i] - mean) / (var ** 0.5) if var > 0 else 0.0
                rows.append(("binance", symbol, _ts(int(d["fundingTime"])), rates[i], z))
            await upsert_funding(rows)
        except Exception as exc:
            print(f"[binance] funding ERR {symbol}: {type(exc).__name__} {str(exc)[:80]}", flush=True)


async def run() -> None:
    symbols = load_universe()
    if not symbols:
        print("[binance] WARNING: universe file empty — nothing to collect", flush=True)
        return
    await upsert_universe("binance", symbols)
    print(f"[binance] collector start: {len(symbols)} symbols, TFs={KLINE_TFS}", flush=True)
    sem = asyncio.Semaphore(CONCURRENCY)
    last_oi_run = 0.0
    last_funding_run = 0.0
    async with httpx.AsyncClient(timeout=30) as client:
        while True:  # continuous loop — collectors are long-lived services
            t0 = time.time()
            await asyncio.gather(*[
                _collect_klines(client, sem, sym, tf)
                for sym in symbols for tf in KLINE_TFS
            ])
            if time.time() - last_oi_run >= OI_LOOP_SEC:
                await asyncio.gather(*[_collect_oi(client, sem, sym) for sym in symbols])
                last_oi_run = time.time()
            if time.time() - last_funding_run >= FUNDING_LOOP_SEC:
                await asyncio.gather(*[_collect_funding(client, sem, sym) for sym in symbols])
                last_funding_run = time.time()
            took = time.time() - t0
            print(f"[binance] cycle done in {took:.1f}s ({len(symbols)} symbols)", flush=True)
            await asyncio.sleep(max(5.0, LOOP_SEC - took))


if __name__ == "__main__":
    asyncio.run(run())
