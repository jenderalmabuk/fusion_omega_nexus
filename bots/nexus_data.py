"""Nexus data adapter — drop-in replacement for backtest.data.fetch_recent().

Queries the Nexus FastAPI instead of Binance REST directly.
Same interface: fetch_recent(symbol, interval, limit) -> pd.DataFrame

Semantics: the Nexus DB only contains CLOSED candles (collectors upsert closed
bars), unlike Binance raw which includes the forming candle. Consumers must NOT
blindly drop the last row — use `drop_forming_bar()` which only drops the last
bar when its open_time falls inside the currently-running interval.
"""

from __future__ import annotations

import datetime as _dt
import logging
import os

import httpx
import pandas as pd

_NEXUS_URL = os.environ.get("NEXUS_API_URL", "http://localhost:8000")

log = logging.getLogger("nexus_data")
if not log.handlers:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

COLUMNS = ["open_time", "open", "high", "low", "close", "volume", "taker_buy_base", "quote_volume"]

# Nexus API column -> engine/backtest column (Binance-raw naming)
_COLUMN_MAP = {
    "taker_buy_vol": "taker_buy_base",
    "quote_vol": "quote_volume",
}

_TF_SECONDS = {
    "1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800,
    "1h": 3600, "2h": 7200, "4h": 14400, "1d": 86400,
}

# Stale-data guard: consider data stale when last open_time lags now by more
# than STALE_MULT x timeframe duration.
STALE_MULT = float(os.environ.get("NEXUS_STALE_MULT", "3"))


def tf_seconds(interval: str) -> int:
    """Timeframe duration in seconds (defaults to 60 for unknown strings)."""
    return _TF_SECONDS.get(interval, 60)


def fetch_recent(symbol: str, interval: str = "1h", limit: int = 300) -> pd.DataFrame:
    """Latest `limit` klines from Nexus. Returns same schema as backtest.data.fetch_recent.

    The returned DataFrame carries metadata in `df.attrs`:
      - "stale": bool  — True when data lag exceeds STALE_MULT x timeframe
      - "lag_sec": float — seconds between now and the last open_time
      - "interval": str
    """
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
                df = _to_df(r.json()["data"])
                _annotate_staleness(df, symbol, interval)
                return df
        except Exception:
            continue

    empty = pd.DataFrame(columns=COLUMNS)
    empty.attrs["stale"] = True
    empty.attrs["lag_sec"] = float("inf")
    empty.attrs["interval"] = interval
    return empty


def _annotate_staleness(df: pd.DataFrame, symbol: str, interval: str) -> None:
    """Compute lag = now_utc - last open_time; mark stale if > STALE_MULT x tf."""
    df.attrs["interval"] = interval
    if df.empty:
        df.attrs["stale"] = True
        df.attrs["lag_sec"] = float("inf")
        return
    last_open = df["open_time"].iloc[-1]
    now = _dt.datetime.utcnow()
    lag = (now - last_open.to_pydatetime()).total_seconds()
    df.attrs["lag_sec"] = lag
    threshold = STALE_MULT * tf_seconds(interval)
    df.attrs["stale"] = lag > threshold
    if df.attrs["stale"]:
        log.warning("STALE DATA %s %s lag=%.0fs (threshold %.0fs)", symbol, interval, lag, threshold)


def drop_forming_bar(df: pd.DataFrame, interval: str) -> pd.DataFrame:
    """Drop the last bar ONLY if it is still forming (open_time inside the running interval).

    Nexus stores closed candles, so normally nothing is dropped. This guard keeps
    compatibility with feeds (e.g. Binance raw) that include the running candle.
    """
    if df.empty:
        return df
    step = tf_seconds(interval)
    last_open = df["open_time"].iloc[-1].to_pydatetime()
    now = _dt.datetime.utcnow()
    if (now - last_open).total_seconds() < step:  # bar not closed yet
        out = df.iloc[:-1].reset_index(drop=True)
        out.attrs.update(df.attrs)
        return out
    return df


def _get_client() -> httpx.Client:
    if not hasattr(_get_client, "_client"):
        _get_client._client = httpx.Client(timeout=httpx.Timeout(15))
    return _get_client._client


def _to_df(rows: list[dict]) -> pd.DataFrame:
    """Convert Nexus API response to same DataFrame format as Binance raw."""
    if not rows:
        return pd.DataFrame(columns=COLUMNS)

    df = pd.DataFrame(rows)

    # Explicit schema mapping (Nexus API -> engine columns) BEFORE default fill,
    # otherwise taker_buy_base/quote_volume silently become 0 and CVD dies.
    for src, dst in _COLUMN_MAP.items():
        if src in df.columns and dst not in df.columns:
            df[dst] = df[src]

    open_times = pd.to_datetime(df["open_time"])

    for col in COLUMNS:
        if col not in df.columns:
            df[col] = 0.0

    for col in ["open", "high", "low", "close", "volume", "taker_buy_base", "quote_volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)

    if float(df["volume"].sum()) > 0 and float(df["taker_buy_base"].abs().sum()) == 0:
        log.warning(
            "taker_buy_base is all-zero while volume > 0 — likely API schema mismatch "
            "(expected taker_buy_vol in Nexus payload)"
        )

    # Strip timezone (engine expects naive datetimes like Binance raw)
    if open_times.dt.tz is not None:
        open_times = open_times.dt.tz_localize(None)
    df["open_time"] = open_times
    out = df[COLUMNS].reset_index(drop=True)
    return out
