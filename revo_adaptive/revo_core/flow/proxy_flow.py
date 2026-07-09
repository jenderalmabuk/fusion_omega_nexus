from __future__ import annotations

import pandas as pd


def add_proxy_flow(df: pd.DataFrame) -> pd.DataFrame:
    """Add price/volume proxy flow fields for backtests without real CVD/OI."""
    close = df['close']
    volume = df['volume']
    vol_ma = volume.rolling(48).mean().replace(0, pd.NA)
    vol_z_proxy = (volume / vol_ma).fillna(1.0)
    cvd_proxy = ((close - close.shift(3)) / close.shift(3).replace(0, pd.NA) * vol_z_proxy).fillna(0.0)
    oi_proxy = volume.pct_change(3).replace([float('inf'), float('-inf')], pd.NA).fillna(0.0) * 100
    df['vol_z_proxy'] = vol_z_proxy
    df['cvd_proxy'] = cvd_proxy
    df['oi_proxy'] = oi_proxy
    return df
