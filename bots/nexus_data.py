"""Nexus data adapter — drop-in replacement for backtest.data.fetch_recent().

Queries the Nexus FastAPI instead of Binance REST directly.
Same interface: fetch_recent(symbol, interval, limit) -> pd.DataFrame
"""

from __future__ import annotations

import os

import httpx
import pandas as pd

_NEXUS_URL = os.environ.get("NEXUS_API_URL", "http://localhost:8000")

COLUMNS = ["open_time", "open", "high", "low", "close", "volume", "taker_buy_base"]


def fetch_recent(symbol: str, interval: str = "1h", limit: int = 300) -> pd.DataFrame:
    """Latest `limit` klines from Nexus. Returns same schema as backtest.data.fetch_recent."""
    client = _get_client()
    tf = interval

    for exchange in ("binance", "bybit"):
        try:
            r = client.get(
                f"{_NEXUS_URL}/klines/{exchange}/{symbol}",
                params={"tf": tf, "limit": limit},
                timeout=15,
            )
            if r.status_code == 200 and r.json().get("count", 0) > 0:
                return _to_df(r.json()["data"])
        except Exception:
            continue

    return pd.DataFrame(columns=COLUMNS)


def _get_client() -> httpx.Client:
    if not hasattr(_get_client, "_client"):
        _get_client._client = httpx.Client(timeout=httpx.Timeout(15))
    return _get_client._client


def _to_df(rows: list[dict]) -> pd.DataFrame:
    """Convert Nexus API response to same DataFrame format as Binance raw."""
    if not rows:
        return pd.DataFrame(columns=COLUMNS)

    df = pd.DataFrame(rows)
    open_times = pd.to_datetime(df["open_time"])

    for col in COLUMNS:
        if col not in df.columns:
            df[col] = 0.0

    for col in ["open", "high", "low", "close", "volume", "taker_buy_base"]:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)

    # Strip timezone (engine expects naive datetimes like Binance raw)
    if open_times.dt.tz is not None:
        open_times = open_times.dt.tz_localize(None)
    df["open_time"] = open_times
    return df[COLUMNS].reset_index(drop=True)
