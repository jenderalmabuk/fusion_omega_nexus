from __future__ import annotations

import os

import numpy as np
import pandas as pd
from pandas import DataFrame

from freqtrade.persistence import Trade
from RevoAdaptiveStrategy import RevoAdaptiveStrategy


class RevoAdaptiveBacktestExitThesis(RevoAdaptiveStrategy):
    """Exit thesis ablation: hard ROI steps vs trailing vs time-based vs partial TP.

    Base config: no regime filter, ATR3 SL (best from prior).

    Exit modes via REVO_EXIT_MODE:
      roi_steps   — hard ROI 8/4/2/0 (current)
      trail_atr   — ATR trailing after 1R (2×ATR trail)
      trail_pct   — % trailing after 1R (1.5% trail)
      time_max    — max hold 4h/6h/8h, then market close
      partial_50  — 50% @ 1R, trail remainder 2×ATR
      partial_33  — 33% @ 1R, 33% @ 2R, trail remainder
    """

    use_exit_signal = True
    use_custom_stoploss = True
    minimal_roi = {"0": 100}  # disable hardcoded ROI, use custom_exit
    process_only_new_candles = True

    # Track partial fills
    _partial_filled: dict[str, float] = {}

    def _cfg(self):
        c = super()._cfg()
        c["exit_mode"] = os.environ.get("REVO_EXIT_MODE", "roi_steps").strip().lower()
        c["trail_atr_mult"] = float(os.environ.get("REVO_TRAIL_ATR_MULT", "2.0"))
        c["trail_pct"] = float(os.environ.get("REVO_TRAIL_PCT", "0.015"))
        c["max_hold_hours"] = int(os.environ.get("REVO_MAX_HOLD_HOURS", "6"))
        c["sl_mode"] = "atr3"
        c["sl_atr_mult"] = 3.0
        return c

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        df = super().populate_indicators(dataframe, metadata)
        if "atr" not in df.columns:
            df["atr"] = self._atr(df, 14)
        df["entry_time"] = pd.to_datetime(df["date"], utc=True)
        return df

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        c = self._cfg()
        dataframe["enter_long"] = 0

        # Pure long-MR base condition (no regime filter)
        base_cond = (
            (dataframe["liq_ok"] == 1)
            & (dataframe["entry_score"] >= c["min_score"])
            & (dataframe["rsi_ok"] == 1)
            & (dataframe["atr_explosive"] == 0)
            & (dataframe["not_falling_knife"] == 1)
        )

        cond = base_cond
        dataframe.loc[cond, "enter_long"] = 1
        dataframe.loc[cond, "enter_tag"] = f"exit:{c['exit_mode']}"
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
        atr = self._get_atr14(pair)
        if atr <= 0:
            return -0.02
        sl_dist = (atr * 3.0) / current_rate
        return -max(0.003, min(sl_dist, 0.08))

    def custom_exit(
        self,
        pair: str,
        trade: Trade,
        current_time,
        current_rate: float,
        current_profit: float,
        **kwargs,
    ):
        c = self._cfg()
        mode = c["exit_mode"]

        # Time-based max hold
        if mode == "time_max":
            hold_hours = (current_time - trade.open_date_utc).total_seconds() / 3600
            if hold_hours >= c["max_hold_hours"]:
                return "time_max_exit"

        # ATR trailing after 1R
        if mode == "trail_atr" and current_profit >= 0.01:
            atr = self._get_atr14(pair)
            if atr > 0:
                trail_dist = (atr * c["trail_atr_mult"]) / current_rate
                if not hasattr(trade, "_trail_price") or current_rate - trail_dist > trade._trail_price:
                    trade._trail_price = current_rate - trail_dist
                if hasattr(trade, "_trail_price") and current_rate <= trade._trail_price:
                    return "trail_atr_exit"

        # % trailing after 1R
        if mode == "trail_pct" and current_profit >= 0.01:
            trail_price = current_rate * (1 - c["trail_pct"])
            if not hasattr(trade, "_trail_price") or trail_price > trade._trail_price:
                trade._trail_price = trail_price
            if hasattr(trade, "_trail_price") and current_rate <= trade._trail_price:
                return "trail_pct_exit"

        return None

    def adjust_trade_position(
        self,
        trade: Trade,
        current_time,
        current_rate: float,
        current_profit: float,
        min_stake: float,
        max_stake: float,
        current_entry_rate: float,
        current_exit_rate: float,
        current_entry_profit: float,
        current_exit_profit: float,
        **kwargs,
    ):
        """Partial TP handling."""
        c = self._cfg()
        mode = c["exit_mode"]

        if mode not in ("partial_50", "partial_33"):
            return None

        filled = self._partial_filled.get(trade.pair, 0)

        if mode == "partial_50":
            # 50% at 1R (~1% profit with 0.25% risk * 4)
            if current_profit >= 0.01 and filled == 0:
                self._partial_filled[trade.pair] = 0.5
                return -trade.stake_amount * 0.5

        if mode == "partial_33":
            if current_profit >= 0.01 and filled == 0:
                self._partial_filled[trade.pair] = 0.33
                return -trade.stake_amount * 0.33
            if current_profit >= 0.02 and filled == 0.33:
                self._partial_filled[trade.pair] = 0.66
                return -trade.stake_amount * 0.33

        return None