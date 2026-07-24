from __future__ import annotations

import numpy as np
import pandas as pd
from pandas import DataFrame

from RevoAdaptiveBacktestBoth import RevoAdaptiveBacktestBoth

BTC_PAIR = "BTC/USDT:USDT"


class RevoAdaptiveBacktestRegime(RevoAdaptiveBacktestBoth):
    """Long+short gated by BTC 15m daily-anchored VWAP regime.

    Regime = sign(BTC_15m_close - BTC_15m_VWAP), where VWAP is daily-anchored
    (UTC session) and computed on 15m bars resampled from the 5m feed.

    Gate:
        LONG  allowed only when BTC regime == +1 (BTC above its 15m VWAP)
        SHORT allowed only when BTC regime == -1 (BTC below its 15m VWAP)

    NO-LOOKAHEAD: each 15m bar's regime is stamped at its CLOSE time
    (bar_open + 15min) and merged backward, so a 5m candle only ever sees a
    fully-completed 15m bar's VWAP -- never the still-forming bar.
    """

    can_short = True

    def _btc_regime(self) -> DataFrame:
        btc = self.dp.get_pair_dataframe(BTC_PAIR, self.timeframe)
        if btc is None or getattr(btc, "empty", True):
            return pd.DataFrame(columns=["date", "btc_regime"])
        b = btc[["date", "high", "low", "close", "volume"]].copy()
        b["date"] = pd.to_datetime(b["date"], utc=True)
        b = b.set_index("date").sort_index()
        # 5m -> 15m
        agg = b.resample("15min").agg(
            {"high": "max", "low": "min", "close": "last", "volume": "sum"}
        ).dropna()
        if agg.empty:
            return pd.DataFrame(columns=["date", "btc_regime"])
        tp = (agg["high"] + agg["low"] + agg["close"]) / 3.0
        pv = tp * agg["volume"]
        day = agg.index.normalize()
        cum_pv = pv.groupby(day).cumsum()
        cum_v = agg["volume"].groupby(day).cumsum().replace(0, np.nan)
        vwap = cum_pv / cum_v
        regime = np.sign(agg["close"] - vwap).fillna(0).astype(int)
        # stamp at bar CLOSE (open + 15min) to prevent lookahead
        avail = agg.index + pd.Timedelta(minutes=15)
        return pd.DataFrame({"date": avail, "btc_regime": regime.values}).reset_index(drop=True)

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        df = super().populate_indicators(dataframe, metadata)
        reg = self._btc_regime()
        if reg.empty:
            df["btc_regime"] = 0
            return df
        left = df.copy()
        # Normalize BOTH merge keys to identical dtype/resolution (freqtrade uses
        # datetime64[ms, UTC]; resample+Timedelta yields [us, UTC]) -> merge_asof
        # requires them equal, else MergeError on incompatible keys.
        left["date"] = pd.to_datetime(left["date"], utc=True).astype("datetime64[ns, UTC]")
        reg = reg.copy()
        reg["date"] = pd.to_datetime(reg["date"], utc=True).astype("datetime64[ns, UTC]")
        merged = pd.merge_asof(
            left[["date"]].sort_values("date"),
            reg.sort_values("date"),
            on="date",
            direction="backward",
        )
        df["btc_regime"] = merged["btc_regime"].fillna(0).astype(int).values
        return df

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        df = super().populate_entry_trend(dataframe, metadata)
        # Regime gate: direction must agree with BTC 15m VWAP regime.
        df.loc[df["btc_regime"] <= 0, "enter_long"] = 0
        df.loc[df["btc_regime"] >= 0, "enter_short"] = 0
        return df
