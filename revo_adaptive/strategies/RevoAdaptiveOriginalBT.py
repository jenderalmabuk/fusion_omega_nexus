from __future__ import annotations

import pandas as pd
from pandas import DataFrame

from RevoAdaptiveStrategy import RevoAdaptiveStrategy


class RevoAdaptiveOriginalBT(RevoAdaptiveStrategy):
    """Backtest form of RevoAdaptiveStrategy (the ORIGINAL bot live on port 8081).

    Inherits everything that decides trade quality and exits:
      minimal_roi ladder {0:0.08, 180:0.04, 360:0.02, 720:0}
      stoploss = -0.02 fixed, use_exit_signal = False, use_custom_stoploss = False
      confirm_trade_entry 12h same-pair loss cooldown (works in backtest)

    ONLY change: entries vectorized over the whole dataframe. The live
    populate_entry_trend sets enter_long on dataframe.index[-1] only -- correct
    when freqtrade calls it once per new candle, but in backtesting it is called
    once per pair over full history, so it yields exactly 0 trades.

    Gate parity with the parent for flow_gate_mode="scoring" (what 8081 runs):
      eff_score  = entry_score + real_flow_long*2 - real_flow_hostile*2
      min_score  = c["min_score"] + 1          <- scoring mode raises the bar
      flow_guard = (real_flow_available == 0) | (real_cvd_z >= -1.5)

    NOTE on flow: revo_flow_context.json is live runtime state with no history,
    so in backtest real_flow_available == 0 for every bar. Consequence:
    flow_guard is always True and the +/-2 score term is always 0, but
    min_score stays at 10 (9+1). That is the faithful "no flow data" branch the
    live strategy itself takes when the file is stale -- not an approximation.

    NOTE on blacklist: the dynamic pair blacklist is state accumulated from live
    losses. Replaying it over history would leak live outcomes into the test, so
    run with REVO_PAIR_BLACKLIST_PATH pointing at a nonexistent file (the
    strategy then treats every pair as not blacklisted, which is also its
    cold-start behaviour).
    """

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        c = self._cfg()
        mode = c["flow_gate_mode"]
        if mode != "scoring":
            raise ValueError(
                f"RevoAdaptiveOriginalBT models flow_gate_mode='scoring' only, got "
                f"'{mode}'. Set REVO_FLOW_GATE_MODE=scoring."
            )

        dataframe["enter_long"] = 0

        eff_score = (
            dataframe["entry_score"]
            + dataframe["real_flow_long"] * 2
            - dataframe["real_flow_hostile"] * 2
        )
        min_score = c["min_score"] + 1

        flow_guard = (
            (dataframe["real_flow_available"] == 0)
            | (dataframe["real_cvd_z"] >= -1.5)
        )

        blacklisted = self._pair_blacklisted(metadata.get("pair", ""))
        if blacklisted:
            blacklist_guard = (
                (dataframe["entry_score"] >= c["blacklist_bypass_score"])
                & (dataframe["rsi"] <= c["blacklist_bypass_rsi"])
                & (dataframe["liq_ok"] == 1)
                & (dataframe["funding_ok"] == 1)
            )
        else:
            blacklist_guard = pd.Series(True, index=dataframe.index)

        cond = (
            (dataframe["liq_ok"] == 1)
            & (eff_score >= min_score)
            & (dataframe["rsi_ok"] == 1)
            & (dataframe["atr_explosive"] == 0)
            & (dataframe["not_falling_knife"] == 1)
            & flow_guard
            & blacklist_guard
        )

        dataframe.loc[cond, "enter_long"] = 1
        dataframe.loc[cond, "enter_tag"] = "revo_original_bt"
        return dataframe
