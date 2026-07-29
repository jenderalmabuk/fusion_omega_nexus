"""Fusion Quantum WebSocket shadow scanner. Read-only; never touches trading state."""
from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Callable

import pandas as pd

from bots.nexus_data import fetch_recent
from fusion_quantum.paper_runner import lifecycle_key, lifecycle_scan, setup_id
from scripts.bybit_ws_observer import observe

AUDIT = Path(os.getenv("FQ_WS_SHADOW_AUDIT", "runtime/fusion_quantum/ws_shadow.jsonl"))
WAIT_SEC = float(os.getenv("FQ_WS_SHADOW_WAIT_SEC", "30"))
CONCURRENCY = int(os.getenv("FQ_WS_SHADOW_CONCURRENCY", "8"))
READY_TIMEOUT_SEC = float(os.getenv("FQ_WS_SHADOW_READY_TIMEOUT_SEC", "180"))
RETRY_SEC = float(os.getenv("FQ_WS_SHADOW_RETRY_SEC", "10"))
_SEM = asyncio.Semaphore(CONCURRENCY)


def candle_ready(last_open: str, expected_start_ms: int) -> bool:
    return pd.Timestamp(last_open) >= pd.Timestamp(expected_start_ms, unit="ms")


def snapshot_at(df: pd.DataFrame, expected_start_ms: int) -> pd.DataFrame:
    cutoff = pd.Timestamp(expected_start_ms, unit="ms", tz="UTC")
    return df[pd.to_datetime(df["open_time"], utc=True) <= cutoff].copy()


def unique_setups(rows: list[dict], key: Callable[[dict], str]) -> list[dict]:
    seen: set[str] = set()
    return [row for row in rows if not (key(row) in seen or seen.add(key(row)))]


def write_audit(row: dict) -> None:
    AUDIT.parent.mkdir(parents=True, exist_ok=True)
    with AUDIT.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, separators=(",", ":"), default=str) + "\n")


async def shadow_scan(event: dict) -> None:
    await asyncio.sleep(WAIT_SEC)
    async with _SEM:
        started = time.time()
        symbol = event["symbol"]
        try:
            waits = 0
            while True:
                htf, ltf = await asyncio.gather(
                    asyncio.to_thread(fetch_recent, symbol, "4h", 1000),
                    asyncio.to_thread(fetch_recent, symbol, "15m", 1000),
                )
                last = str(ltf.open_time.iloc[-1]) if len(ltf) else None
                ready = bool(last) and candle_ready(last, event["start"])
                if ready or started + READY_TIMEOUT_SEC <= time.time():
                    break
                waits += 1
                await asyncio.sleep(RETRY_SEC)
            htf = snapshot_at(htf, event["start"])
            ltf = snapshot_at(ltf, event["start"])
            lifecycle = lifecycle_scan(symbol, htf, ltf)
            setups = unique_setups(lifecycle.setups, setup_id)
            consumed = unique_setups(lifecycle.consumed, lambda x: lifecycle_key(x) or "")
            write_audit({
                "event": "shadow_scan", "observer_only": True, "symbol": symbol,
                "candle_start": event["start"], "ws_received_at": event["received_at"],
                "scan_finished_at": time.time(), "scan_sec": time.time() - started,
                "data_15m_last": last, "candle_ready": ready, "ready_waits": waits,
                "stale": bool(htf.attrs.get("stale") or ltf.attrs.get("stale")),
                "setup_ids": [setup_id(x) for x in setups], "setups": setups,
                "consumed_zone_ids": [lifecycle_key(x) for x in consumed],
                "failed_zone_ids": [lifecycle_key(x) for x in consumed if x.get("outcome") == "failed_confirmation"],
                "consumed": consumed,
            })
        except Exception as exc:
            write_audit({"event": "shadow_error", "observer_only": True, "symbol": symbol,
                         "error": repr(exc), "at": time.time()})


async def main() -> None:
    path = os.getenv("FQ_UNIVERSE_FILE", "runtime/revo/canonical_universe.txt")
    symbols = sorted(set(x.strip().upper() for x in open(path) if x.strip()))
    await observe(symbols, int(os.getenv("FQ_WS_SHADOW_DURATION", "1100")), shadow_scan)


if __name__ == "__main__":
    asyncio.run(main())
