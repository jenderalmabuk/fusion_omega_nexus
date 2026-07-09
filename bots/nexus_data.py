"""Nexus data adapter — drop-in replacement for backtest.data.fetch_recent().

Queries the Nexus FastAPI instead of Binance REST directly.
Same interface: fetch_recent(symbol, interval, limit) -> pd.DataFrame

Notes:
 * Nexus only stores CLOSED candles (collector inserts after close), so the
   returned frame normally contains no forming candle.
 * The API returns columns `taker_buy_vol` / `quote_vol`; the engine expects
   `taker_buy_base` / `quote_volume` — mapped explicitly in _to_df().
 * Stale-data guard: df.attrs["lag_sec"] / df.attrs["stale"] are set so the
   engine can skip symbols whose data feed has frozen.
"""

from __future__ import annotations

import datetime as _dt
import os

import httpx
import pandas as pd

_NEXUS_URL = os.environ.get("NEXUS_API_URL", "http://localhost:8000")

COLUMNS = ["open_time", "open", "high", "low", "close", "volume", "taker_buy_base"]

# Explicit Nexus API -> engine column mapping (schema desync guard)
_COLUMN_MAP = {
    "taker_buy_vol": "taker_buy_base",
    "quote_vol": "quote_volume",
}

TF_SEC = {
    "1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800,
    "1h": 3600, "4h": 14400, "1d": 86400,
}


def fetch_recent(symbol: str, interval: str = "1h", limit: int = 300) -> pd.DataFrame:
    """Latest `limit` klines from Nexus. Returns same schema as backtest.data.fetch_recent."""
    client = _get_client()
    tf = interval

    df = pd.DataFrame(columns=COLUMNS)
    for exchange in ("binance", "bybit"):
        try:
            r = client.get(
                f"{_NEXUS_URL}/klines/{exchange}/{symbol}",
                params={"tf": tf, "limit": limit},
                timeout=15,
            )
            if r.status_code == 200 and r.json().get("count", 0) > 0:
                df = _to_df(r.json()["data"], symbol=symbol, tf=tf)
                break
        except Exception:
            continue

    # ── Stale-data guard: lag = now_utc - last open_time ──
    lag_sec = None
    stale = False
    if len(df) > 0:
        last_open = pd.Timestamp(df["open_time"].iloc[-1])
        now = pd.Timestamp(_dt.datetime.now(_dt.timezone.utc)).tz_localize(None)
        lag_sec = float((now - last_open).total_seconds())
        tf_dur = TF_SEC.get(tf, 60)
        if lag_sec > 3 * tf_dur:
            stale = True
            print(f"[WARN] STALE DATA {symbol} {tf} lag={int(lag_sec)}s "
                  f"(> {3 * tf_dur}s = 3x timeframe)")
    df.attrs["lag_sec"] = lag_sec
    df.attrs["stale"] = stale
    return df


def _get_client() -> httpx.Client:
    if not hasattr(_get_client, "_client"):
        _get_client._client = httpx.Client(timeout=httpx.Timeout(15))
    return _get_client._client


def _to_df(rows: list[dict], symbol: str = "?", tf: str = "?") -> pd.DataFrame:
    """Convert Nexus API response to same DataFrame format as Binance raw."""
    if not rows:
        return pd.DataFrame(columns=COLUMNS)

    df = pd.DataFrame(rows)

    # Explicit schema mapping BEFORE any default-filling: the Nexus API returns
    # taker_buy_vol / quote_vol, the engine expects taker_buy_base / quote_volume.
    for src, dst in _COLUMN_MAP.items():
        if src in df.columns and dst not in df.columns:
            df[dst] = df[src]

    open_times = pd.to_datetime(df["open_time"])

    for col in COLUMNS:
        if col not in df.columns:
            df[col] = 0.0

    for col in ["open", "high", "low", "close", "volume", "taker_buy_base"]:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)

    # Schema-mismatch alarm: taker_buy_base all zero while volume is non-zero
    # means the CVD source silently degraded (this is exactly the bug that made
    # CVD always 0). Loud warning so it never happens silently again.
    if float(df["volume"].sum()) > 0 and float(df["taker_buy_base"].abs().sum()) == 0:
        print(f"[WARN] SCHEMA MISMATCH {symbol} {tf}: taker_buy_base is all-zero "
              f"while volume > 0 — CVD will be 0 (check API column names)")

    # Strip timezone (engine expects naive datetimes like Binance raw)
    if open_times.dt.tz is not None:
        open_times = open_times.dt.tz_localize(None)
    df["open_time"] = open_times

    keep = COLUMNS + (["quote_volume"] if "quote_volume" in df.columns else [])
    out = df[keep].reset_index(drop=True)
    if "quote_volume" in out.columns:
        out["quote_volume"] = pd.to_numeric(out["quote_volume"], errors="coerce").fillna(0.0)
    return out
