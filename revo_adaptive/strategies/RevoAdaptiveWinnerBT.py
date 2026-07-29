from __future__ import annotations

from pandas import DataFrame

from RevoAdaptiveWinner import RevoAdaptiveWinner


class RevoAdaptiveWinnerBT(RevoAdaptiveWinner):
    """Backtest form of RevoAdaptiveWinner (the bot live on port 8083).

    Inherits EVERYTHING that decides trade quality from RevoAdaptiveWinner:
      indicators, entry_score, SL = 3.0 x ATR14 clamp [0.3%, 8%],
      stoploss = -0.08 ceiling, time-stop exit, ROI ladder disabled.

    ONLY change: entries are vectorized over the whole dataframe. The live
    populate_entry_trend sets enter_long on dataframe.index[-1] only — correct
    when freqtrade calls it once per new candle, but in backtesting it is called
    once per pair over the full history, so it yields exactly 0 trades. That is
    why this subclass exists, and why the c0 smoke test reported 0 trades.

    Gate parity with the parent, for flow_gate_mode="pure" (what the live bot
    runs, REVO_FLOW_GATE_MODE=pure):
      - flow_guard      -> True (pure = no flow gate at all)
      - blacklist_guard -> True (live bot runs with no blacklist path set)
      - eff_score       -> entry_score, min_score unmodified (no "scoring" bonus)
    Anything other than pure mode is rejected rather than silently approximated.
    """

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        c = self._cfg()
        mode = c["flow_gate_mode"]
        if mode != "pure":
            raise ValueError(
                f"RevoAdaptiveWinnerBT models flow_gate_mode='pure' only, got '{mode}'. "
                "Set REVO_FLOW_GATE_MODE=pure or use RevoAdaptiveBacktest instead."
            )

        dataframe["enter_long"] = 0
        cond = (
            (dataframe["liq_ok"] == 1)
            & (dataframe["entry_score"] >= c["min_score"])
            & (dataframe["rsi_ok"] == 1)
            & (dataframe["atr_explosive"] == 0)
            & (dataframe["not_falling_knife"] == 1)
        )
        dataframe.loc[cond, "enter_long"] = 1
        dataframe.loc[cond, "enter_tag"] = "revo_winner_bt"
        return dataframe
