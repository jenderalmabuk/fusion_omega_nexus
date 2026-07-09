"""Nexus data adapter — drop-in replacement for backtest.data.fetch_recent().

Queries the Nexus FastAPI instead of Binance REST directly.
Same interface: fetch_recent(symbol, interval, limit) -> pd.DataFrame

Notes:
- The Nexus DB only stores CLOSED candles (unlike Binance raw which includes
  the forming candle). Callers must NOT blindly drop the last row.
- API column names differ from Binance raw: taker_buy_vol -> taker_buy_base,
  quote_vol -> quote_volume. Mapped explicitly in _to_df.
- Stale data detection: if the newest candle is older than 3x the timeframe,
  the returned DataFrame carries attrs["stale"] = True.
"""

from __future__ import annotations

import datetime as dt
import os

import httpx
import pandas as pd

_NEXUS_URL = os.environ.get("NEXUS_API_URL", "http://localhost:8000")

COLUMNS = ["open_time", "open", "high", "low", "close", "volume", "taker_buy_base"]

# Explicit Nexus API -> engine schema column mapping (see api/main.py SELECT)
_COLUMN_MAP = {
    "taker_buy_vol": "taker_buy_base",
    "quote_vol": "quote_volume",
}

_TF_MINUTES = {"1m": 1, "3m": 3, "5m": 5, "15m": 15, "30m": 30,
               "1h": 60, "2h": 120, "4h": 240, "1d": 1440}


def _tf_minutes(interval: str) -> int:
    return _TF_MINUTES.get(interval, 60)


def is_stale(df: pd.DataFrame) -> bool:
    """True if the DataFrame was flagged stale by fetch_recent."""
    return bool(df.attrs.get("stale", False))


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
                df = _to_df(r.json()["data"], symbol=symbol, tf=tf)
                _flag_stale(df, symbol, tf)
                return df
        except Exception:
            continue

    return pd.DataFrame(columns=COLUMNS)


def _flag_stale(df: pd.DataFrame, symbol: str, tf: str) -> None:
    """Mark df.attrs['stale']=True when last candle is older than 3x timeframe."""
    if df.empty:
        return
    try:
        last_open = df["open_time"].iloc[-1]
        now = dt.datetime.utcnow()
        lag = now - last_open.to_pydatetime()
        limit = dt.timedelta(minutes=3 * _tf_minutes(tf))
        if lag > limit:
            print(f"[WARN] STALE DATA {symbol} {tf} lag={lag}")
            df.attrs["stale"] = True
    except Exception:
        pass


def _get_client() -> httpx.Client:
    if not hasattr(_get_client, "_client"):
        _get_client._client = httpx.Client(timeout=httpx.Timeout(15))
    return _get_client._client


def _to_df(rows: list[dict], symbol: str = "?", tf: str = "?") -> pd.DataFrame:
    """Convert Nexus API response to same DataFrame format as Binance raw."""
    if not rows:
        return pd.DataFrame(columns=COLUMNS)

    df = pd.DataFrame(rows)

    # Explicit API -> engine schema mapping BEFORE default-fill below
    for src, dst in _COLUMN_MAP.items():
        if src in df.columns and dst not in df.columns:
            df[dst] = df[src]

    open_times = pd.to_datetime(df["open_time"])

    for col in COLUMNS:
        if col not in df.columns:
            df[col] = 0.0

    for col in ["open", "high", "low", "close", "volume", "taker_buy_base"]:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)

    # Schema-mismatch canary: volume present but taker_buy_base all zero
    if len(df) > 0 and float(df["volume"].sum()) > 0 and float(df["taker_buy_base"].abs().sum()) == 0:
        print(f"[WARN] taker_buy_base all zero for {symbol} {tf} despite volume>0 — "
              f"possible API schema mismatch (expected taker_buy_vol)")

    # Strip timezone (engine expects naive datetimes like Binance raw)
    if open_times.dt.tz is not None:
        open_times = open_times.dt.tz_localize(None)
    df["open_time"] = open_times
    out = df[COLUMNS].reset_index(drop=True)
    # carry quote_volume through if present (used by adversarial context/liquidity filter)
    if "quote_volume" in df.columns:
        out = out.copy()
        out["quote_volume"] = pd.to_numeric(df["quote_volume"], errors="coerce").fillna(0.0).values
    return out
