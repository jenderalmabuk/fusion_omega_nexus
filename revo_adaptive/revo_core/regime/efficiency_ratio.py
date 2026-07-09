from __future__ import annotations
import pandas as pd


def efficiency_ratio(close: pd.Series, window: int = 48) -> pd.Series:
    direction = (close - close.shift(window)).abs()
    volatility = close.diff().abs().rolling(window).sum().replace(0, pd.NA)
    return (direction / volatility).clip(0, 1).fillna(0.0)
