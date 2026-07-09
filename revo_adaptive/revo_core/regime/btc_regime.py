from __future__ import annotations
import pandas as pd


def classify_btc_regime(close: pd.Series, high: pd.Series | None = None, low: pd.Series | None = None) -> pd.DataFrame:
    ema50 = close.ewm(span=50, adjust=False).mean()
    ema200 = close.ewm(span=200, adjust=False).mean()
    ret_1h = close.pct_change(12).fillna(0) * 100  # 12x5m = 1h
    regime = pd.Series('neutral', index=close.index, dtype='object')
    regime[(close > ema50) & (ema50 > ema200)] = 'risk_on'
    regime[(close < ema50) & (ema50 < ema200)] = 'risk_off'
    regime[ret_1h < -2.0] = 'panic'
    return pd.DataFrame({'btc_ema50': ema50, 'btc_ema200': ema200, 'btc_ret_1h_pct': ret_1h, 'btc_regime': regime})
