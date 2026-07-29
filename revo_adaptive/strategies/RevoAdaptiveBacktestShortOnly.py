from __future__ import annotations

import os

import numpy as np
import pandas as pd
from pandas import DataFrame

from freqtrade.persistence import Trade
from RevoAdaptiveStrategy import RevoAdaptiveStrategy


class RevoAdaptiveBacktestShortOnly(RevoAdaptiveStrategy):
    """Short-only mean-reversion ablation.

    Tests if short MR has edge (memory says shorts lose both regimes, but verify).
    Uses same long-MR score gates, inverted for short.
    SL modes: fixed / atr2 / atr3 (best from prior ablation)
    """

    can_short = True
    use_custom_stoploss = True
    process_only_new_candles = True

    def _cfg(self):
        c = super()._cfg()
        c["sl_mode"] = os.environ.get("REVO_SL_MODE", "fixed").strip().lower()
        c["sl_atr_mult"] = float(os.environ.get("REVO_SL_ATR_MULT", "0"))
        return c

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        df = super().populate_indicators(dataframe, metadata)
        if "atr" not in df.columns:
            df["atr"] = self._atr(df, 14)
        return df

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        c = self._cfg()
        dataframe["enter_long"] = 0
        dataframe["enter_short"] = 0
        # Short MR: same score gates, inverted pullback logic
        # pair_uptrend_pullback = 1 when price pulls back in uptrend (long setup)
        # For short: we want price rally in downtrend = "downtrend pullback"
        # Use: not pair_uptrend_pullback + btc_ok inverted + same score threshold
        short_cond = (
            (dataframe["liq_ok"] == 1)
            & (dataframe["entry_score"] >= c["min_score"])
            & (dataframe["rsi_ok"] == 1)
            & (dataframe["atr_explosive"] == 0)
            & (dataframe["not_falling_knife"] == 1)
            # Invert trend filter: enter short when NOT in uptrend pullback
            & (dataframe["pair_uptrend_pullback"] == 0)
            # BTC filter: short when BTC not bullish
            & (dataframe["btc_ok"] == 0)
        )
        dataframe.loc[short_cond, "enter_short"] = 1
        dataframe.loc[short_cond, "enter_tag"] = f"short_only:{c['sl_mode']}"
        return dataframe

    def _get_atr14(self, pair: str) -> float:
        df = self.dp.get_pair_dataframe(pair, self.timeframe)
        if df is not None and not df.empty:
            atr_series = self._atr(df, 14)
            if not atr_series.empty:
                return float(atr_series.iloc[-1])
        return 0.0

    def custom_stoploss(
        self,
        pair: str,
        trade: Trade,
        current_time,
        current_rate: float,
        current_profit: float,
        **kwargs,
    ) -> float:
        c = self._cfg()
        mode = c["sl_mode"]
        mult = c["sl_atr_mult"]
        if mode == "fixed" or mult <= 0:
            return -0.02
        atr = self._get_atr14(pair)
        if atr <= 0:
            return -0.02
        sl_dist = (atr * mult) / current_rate
        return -max(0.003, min(sl_dist, 0.08))