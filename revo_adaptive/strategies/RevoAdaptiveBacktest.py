from __future__ import annotations

from pandas import DataFrame

from RevoAdaptiveStrategy import RevoAdaptiveStrategy


class RevoAdaptiveBacktest(RevoAdaptiveStrategy):
    """Backtest-only variant of RevoAdaptiveStrategy.

    Inherits ALL indicator/scoring logic from the live strategy (populate_indicators),
    so entry-quality parameters (MIN_SCORE, DISCOUNT, RSI_MAX, ...) are identical.

    The ONLY difference: entries are vectorized across the full dataframe instead of
    the live "current-candle-only + shotgun guard" runtime pattern, which produces
    0 trades in freqtrade backtesting (that pattern only sets enter_long on the last
    row). This subclass is the faithful backtest representation of the SIGNAL logic;
    portfolio caps are handled by max_open_trades.
    """

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        c = self._cfg()
        dataframe["enter_long"] = 0

        flow_guard = (
            (dataframe["real_flow_available"] == 0)
            | (dataframe["real_flow_long"] == 1)
        )
        cond = (
            (dataframe["liq_ok"] == 1)
            & (dataframe["entry_score"] >= c["min_score"])
            & (dataframe["rsi_ok"] == 1)
            & (dataframe["atr_explosive"] == 0)
            & flow_guard
        )

        dataframe.loc[cond, "enter_long"] = 1
        dataframe.loc[cond, "enter_tag"] = "revo_adaptive_bt"
        return dataframe
