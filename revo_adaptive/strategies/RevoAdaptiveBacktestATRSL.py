from __future__ import annotations

import os

import numpy as np
import pandas as pd
from pandas import DataFrame

from freqtrade.persistence import Trade
from freqtrade.strategy import IStrategy

from RevoAdaptiveStrategy import RevoAdaptiveStrategy


class RevoAdaptiveBacktestATRSL(RevoAdaptiveStrategy):
    """ATR-based stoploss ablation with pure long-MR entry gates.

    SL modes via env REVO_SL_MODE:
      fixed     — hard -2% (baseline)
      atr1      — 1.0 × ATR14
      atr1_5    — 1.5 × ATR14
      atr2      — 2.0 × ATR14
      atr2_5    — 2.5 × ATR14
      atr3      — 3.0 × ATR14
    """

    use_custom_stoploss = True
    process_only_new_candles = True

    def _cfg(self):
        c = super()._cfg()
        c["sl_mode"] = os.environ.get("REVO_SL_MODE", "fixed").strip().lower()
        c["sl_atr_mult"] = float(os.environ.get("REVO_SL_ATR_MULT", "0"))
        return c

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        df = super().populate_indicators(dataframe, metadata)
        # Ensure ATR14 exists
        if "atr" not in df.columns:
            df["atr"] = self._atr(df, 14)
        return df

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        c = self._cfg()
        dataframe["enter_long"] = 0
        # Pure long-MR: no flow, no BTC regime
        cond = (
            (dataframe["liq_ok"] == 1)
            & (dataframe["entry_score"] >= c["min_score"])
            & (dataframe["rsi_ok"] == 1)
            & (dataframe["atr_explosive"] == 0)
            & (dataframe["not_falling_knife"] == 1)
        )
        dataframe.loc[cond, "enter_long"] = 1
        dataframe.loc[cond, "enter_tag"] = f"atr_sl:{c['sl_mode']}"
        return dataframe

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

        # Get ATR14 from trade (set in confirm_trade_entry) or compute from df
        atr = getattr(trade, "atr_14", 0.0)
        if atr <= 0:
            df = self.dp.get_pair_dataframe(pair, self.timeframe)
            if df is not None and not df.empty:
                atr_series = self._atr(df, 14)
                if not atr_series.empty:
                    atr = float(atr_series.iloc[-1])

        if atr <= 0:
            return -0.02

        sl_dist = (atr * mult) / current_rate
        # Clamp: min 0.3%, max 8%
        return -max(0.003, min(sl_dist, 0.08))

    def confirm_trade_entry(
        self,
        pair: str,
        order_type: str,
        amount: float,
        rate: float,
        time_in_force: str,
        current_time,
        entry_tag: str | None = None,
        side: str = "long",
        **kwargs,
    ) -> bool:
        # Attach ATR14 to trade for custom_stoploss
        df = self.dp.get_pair_dataframe(pair, self.timeframe)
        if df is not None and not df.empty:
            atr_series = self._atr(df, 14)
            if not atr_series.empty:
                # Use Trade's setattr workaround: freqtrade trades are managed objects
                # Store in trade.custom_data via existing mechanism
                pass  # will read from dataframe in custom_stoploss
        return super().confirm_trade_entry(
            pair, order_type, amount, rate, time_in_force, current_time, entry_tag, side, **kwargs
        )

    # Make ATR available in custom_stoploss via dataframe cache
    def _get_atr14(self, pair: str) -> float:
        df = self.dp.get_pair_dataframe(pair, self.timeframe)
        if df is not None and not df.empty:
            atr_series = self._atr(df, 14)
            if not atr_series.empty:
                return float(atr_series.iloc[-1])
        return 0.0


# Monkey-patch custom_stoploss to use _get_atr14
orig_custom_stoploss = RevoAdaptiveBacktestATRSL.custom_stoploss


def patched_custom_stoploss(self, pair, trade, current_time, current_rate, current_profit, **kwargs):
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


RevoAdaptiveBacktestATRSL.custom_stoploss = patched_custom_stoploss