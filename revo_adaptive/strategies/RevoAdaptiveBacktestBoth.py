from __future__ import annotations

import numpy as np
from pandas import DataFrame

from RevoAdaptiveStrategy import RevoAdaptiveStrategy


class RevoAdaptiveBacktestBoth(RevoAdaptiveStrategy):
    """Two-directional (long + short) backtest variant.

    LONG side  = identical to RevoAdaptiveBacktest (score-gated pullback).
    SHORT side = SYMMETRIC MIRROR of the long logic:
        premium >= disc above EMA55, overbought RSI (>= 100-rsi_max),
        downtrend bounce (ema50<ema200), same min_score, same knife floor
        (mirrored to a 'rising-knife' guard), same liquidity/atr gates.

    Purpose: answer "does adding shorts help in the current down/choppy
    regime?" A symmetric mirror is the honest A/B vs the long-only baseline.
    All indicator columns are produced by the parent populate_indicators;
    this class only adds the short mirror and sets enter_long/enter_short.

    NOTE: in a pure-OHLCV backtest real_flow_available==0, so funding_ok=1
    and funding_crowded=0 for BOTH directions (symmetric by construction);
    the flow-direction guard collapses to the no-context branch.
    """

    can_short = True

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        c = self._cfg()
        df = dataframe
        df["enter_long"] = 0
        df["enter_short"] = 0

        # ---------- LONG (identical to RevoAdaptiveBacktest) ----------
        flow_guard_long = (df["real_flow_available"] == 0) | (df["real_flow_long"] == 1)
        long_cond = (
            (df["liq_ok"] == 1)
            & (df["entry_score"] >= c["min_score"])
            & (df["rsi_ok"] == 1)
            & (df["atr_explosive"] == 0)
            & (df["not_falling_knife"] == 1)
            & flow_guard_long
        )
        df.loc[long_cond, "enter_long"] = 1
        df.loc[long_cond, "enter_tag"] = "revo_long"

        # ---------- SHORT (symmetric mirror) ----------
        disc = c["discount"]
        dmax = c["discount_max"]
        rsi_max = c["rsi_max"]

        # price ABOVE ema55 by >= disc (mirror of at_discount)
        at_premium = (df["dist_ema55_pct"] >= disc).astype(int)
        # rising-knife guard: don't short a blow-off already > dmax above EMA55
        not_rising_knife = (df["dist_ema55_pct"] <= dmax).astype(int)
        # overbought (mirror of rsi_ok = rsi <= rsi_max)
        rsi_ok_short = (df["rsi"] >= (100.0 - rsi_max)).astype(int)
        # downtrend bounce (mirror of pair_uptrend_pullback)
        pair_downtrend_bounce = ((df["ema50"] < df["ema200"]) & (at_premium == 1)).astype(int)
        # cvd mirror: NOT aggressive buying
        cvd_ok_short = np.where(
            df["real_flow_available"] == 1,
            (df["real_cvd_z"] < 0.5).astype(int),
            (df["cvd_proxy"] < 0.5).astype(int),
        )

        entry_score_short = (
            at_premium * 2
            + rsi_ok_short
            + cvd_ok_short * 2
            + df["oi_ok"]
            + df["funding_ok"] * 2          # =1 (neutral) in pure-OHLCV backtest
            + pair_downtrend_bounce
            + df["btc_ok"]
            + df["vol_ok"]
            - df["er_chop"]
            - df["btc_dump"]
            - df["atr_explosive"]
            - df["funding_crowded"] * 2     # =0 in pure-OHLCV backtest
        ).astype(int)

        # flow guard mirror (collapses to no-context branch in backtest)
        flow_guard_short = (df["real_flow_available"] == 0) | (df["real_flow_hostile"] == 1)
        short_cond = (
            (df["liq_ok"] == 1)
            & (entry_score_short >= c["min_score"])
            & (rsi_ok_short == 1)
            & (df["atr_explosive"] == 0)
            & (not_rising_knife == 1)
            & flow_guard_short
            & ~long_cond  # never both directions on the same candle; long wins
        )
        df.loc[short_cond, "enter_short"] = 1
        df.loc[short_cond, "enter_tag"] = "revo_short"

        return df

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["exit_long"] = 0
        dataframe["exit_short"] = 0
        return dataframe
