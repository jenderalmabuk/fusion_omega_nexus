#!/usr/bin/env python3
"""Backfill engine-critical Binance klines so adaptive scan can classify HOT/WARM/COLD."""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from pathlib import Path

import asyncpg
import httpx

DB = os.getenv("DATABASE_URL", "postgresql://nexus:nexus_pass@localhost:5432/nexus")
UNIVERSE = Path(os.getenv("UNIVERSE_FILE", Path(__file__).resolve().parent / "universe.txt"))
TFS = ["5m", "15m", "30m", "1h"]
LIMIT = 320
BASE = "https://fapi.binance.com/fapi/v1/klines"


def row(symbol: str, tf: str, k: list) -> tuple:
    return (
        "binance", symbol, tf,
        datetime.fromtimestamp(k[0] / 1000, tz=timezone.utc),
        float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[5]),
        datetime.fromtimestamp(k[6] / 1000, tz=timezone.utc),
        float(k[7]), int(k[8]), float(k[9]), float(k[10]),
    )


async def fetch(client: httpx.AsyncClient, symbol: str, tf: str) -> list[list]:
    r = await client.get(BASE, params={"symbol": symbol, "interval": tf, "limit": LIMIT})
    r.raise_for_status()
    return r.json()


async def insert(conn, rows: list[tuple]) -> None:
    if not rows:
        return
    await conn.executemany(
        """
        INSERT INTO klines (exchange, symbol, timeframe, open_time, open, high, low, close,
                            volume, close_time, quote_vol, trades, taker_buy_vol, taker_buy_quote_vol)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14)
        ON CONFLICT DO NOTHING
        """,
        rows,
    )


async def main() -> None:
    symbols = UNIVERSE.read_text().split()
    pool = await asyncpg.create_pool(DB, min_size=1, max_size=4)
    sem = asyncio.Semaphore(8)
    done = 0
    async with httpx.AsyncClient(timeout=30) as client:
        async with pool.acquire() as conn:
            for symbol in symbols:
                for tf in TFS:
                    async with sem:
                        try:
                            data = await fetch(client, symbol, tf)
                            await insert(conn, [row(symbol, tf, k) for k in data])
                        except Exception as e:
                            print(f"ERR {symbol} {tf}: {type(e).__name__} {str(e)[:80]}", flush=True)
                        await asyncio.sleep(0.06)
                done += 1
                if done % 25 == 0:
                    print(f"backfilled {done}/{len(symbols)} symbols", flush=True)
    await pool.close()
    print(f"DONE backfilled {len(symbols)} symbols x {len(TFS)} TFs limit={LIMIT}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
