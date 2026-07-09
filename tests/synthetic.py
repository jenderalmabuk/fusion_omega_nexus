"""Deterministic synthetic OHLCV builders containing exactly ONE valid
OB + FVG (imbalance) pattern, used by the live==backtest regression tests.

Construction notes (BULL):
 * zone TF (30m, 300 bars): smooth uptrend; at index 200 a red OB candle with a
   bullish FVG (high[200] < low[202]) and a break of structure. Never
   invalidated (no zone close below zlow afterwards).
 * LTF (5m, 400 bars): smooth drift into the zone; at j=395 a 3-candle bullish
   imbalance (high[394] < low[396], green middle candle) whose leg intersects
   the OB zone. ce=396 => only 3 bars old (inside FIB_EXPIRY=12).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

ZONE_OB_IDX = 200
LTF_IMB_CE = 396  # candle-3 completion index of the injected imbalance


def _smooth(n: int, start: float, step: float, t0: pd.Timestamp, freq: str) -> pd.DataFrame:
    """Monotonic gently-rising candles that can NEVER form an FVG or a red OB."""
    opens = start + step * np.arange(n)
    closes = opens + step  # green candles
    highs = closes + 0.1
    lows = opens - 0.1
    times = pd.date_range(t0, periods=n, freq=freq)
    return pd.DataFrame({
        "open_time": times, "open": opens, "high": highs, "low": lows,
        "close": closes, "volume": np.full(n, 1000.0),
        "taker_buy_base": np.full(n, 600.0),
    })


def make_zone_df(n: int = 300) -> pd.DataFrame:
    t0 = pd.Timestamp("2026-01-01 00:00:00")
    df = _smooth(n, 100.0, 0.05, t0, "30min")
    i = ZONE_OB_IDX
    # Red OB candle: zlow=117.5, zhigh=120.5
    df.loc[i, ["open", "close", "high", "low"]] = [120.0, 118.0, 120.5, 117.5]
    # Impulse up (BOS: high 128 > all prior highs ~110)
    df.loc[i + 1, ["open", "close", "high", "low"]] = [118.0, 124.0, 125.0, 118.0]
    # FVG candle: low 121 > high[i]=120.5
    df.loc[i + 2, ["open", "close", "high", "low"]] = [124.0, 126.0, 127.0, 121.0]
    df.loc[i + 3, ["open", "close", "high", "low"]] = [126.0, 127.5, 128.0, 125.5]
    # Stay ABOVE zlow afterwards (no invalidation) while keeping the uptrend
    for k in range(i + 4, n):
        base = 127.5 + 0.02 * (k - i - 4)
        df.loc[k, ["open", "close", "high", "low"]] = [base, base + 0.02, base + 0.12, base - 0.12]
    return df


def make_ltf_df(n: int = 400) -> pd.DataFrame:
    zone_end = pd.Timestamp("2026-01-01 00:00:00") + pd.Timedelta(minutes=30 * 299)
    t0 = zone_end - pd.Timedelta(minutes=5 * (n - 1))
    df = _smooth(n, 110.0, 0.02, t0, "5min")
    j = LTF_IMB_CE - 1  # middle candle of the 3-candle imbalance
    # candle 1 (j-1): high 118.0, low 117.6  -> leg_low = 117.6
    df.loc[j - 1, ["open", "close", "high", "low"]] = [117.9, 117.7, 118.0, 117.6]
    # candle 2 (j): green middle
    df.loc[j, ["open", "close", "high", "low"]] = [117.8, 119.0, 119.1, 117.75]
    # candle 3 (j+1=ce): low 118.3 > high[j-1]=118.0 (bullish FVG), high 119.5 -> leg_high
    df.loc[j + 1, ["open", "close", "high", "low"]] = [118.9, 119.2, 119.5, 118.3]
    # remaining bars: hover near entry so ENTRY_TOO_FAR (1.5%) never triggers
    for k in range(j + 2, n):
        df.loc[k, ["open", "close", "high", "low"]] = [118.8, 118.85, 118.95, 118.7]
    return df
