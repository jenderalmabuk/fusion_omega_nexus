from __future__ import annotations

import os

from pandas import DataFrame

from freqtrade.persistence import Trade

from RevoAdaptiveStrategy import RevoAdaptiveStrategy


class RevoAdaptiveWinner(RevoAdaptiveStrategy):
    """IS+OOS validated Revo reversion config, live-paper form.

    Entry gates inherited from RevoAdaptiveStrategy (score>=9, discount 3.5-6%,
    RSI<=40, atr_pct<=4, er_chop 0.15, falling-knife veto) with
    REVO_FLOW_GATE_MODE=pure so no flow gate applies.

    Exit thesis from the ablation winner (OOS Jun25-Jul10: 95 trades, WR 37.9%,
    PF 1.41, DD 1.02%):
      SL   = 3.0 x ATR14, clamped [0.3%, 8%]
      exit = hard time stop at REVO_MAX_HOLD_HOURS (default 4h)
    ROI ladder is disabled so the time stop is the only profit-side exit.
    """

    use_exit_signal = True
    use_custom_stoploss = True
    minimal_roi = {"0": 100}  # disable ROI ladder; time stop governs exits
    # MUST be wider than any value custom_stoploss can return: freqtrade clamps
    # custom_stoploss to this class attribute, it can never loosen it. Inheriting
    # the parent's -0.02 silently capped the ATR3x stop at 2% and produced three
    # 1-2 minute stop_loss exits at exactly -2.00% on 2026-07-28. custom_stoploss
    # clamps to 8% max, so -0.08 is the matching ceiling.
    stoploss = -0.08

    def _cfg(self):
        c = super()._cfg()
        c["sl_atr_mult"] = float(os.environ.get("REVO_SL_ATR_MULT", "3.0"))
        c["max_hold_hours"] = float(os.environ.get("REVO_MAX_HOLD_HOURS", "4"))
        return c

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        df = super().populate_indicators(dataframe, metadata)
        if "atr" not in df.columns:
            df["atr"] = self._atr(df, 14)
        return df

    def _atr14(self, pair: str) -> float:
        df = self.dp.get_pair_dataframe(pair, self.timeframe)
        if df is None or df.empty:
            return 0.0
        s = self._atr(df, 14)
        return float(s.iloc[-1]) if not s.empty else 0.0

    def custom_stoploss(self, pair: str, trade: Trade, current_time,
                        current_rate: float, current_profit: float, **kwargs) -> float:
        atr = self._atr14(pair)
        if atr <= 0 or current_rate <= 0:
            return -0.02  # fall back to the inherited fixed stop
        sl_dist = (atr * self._cfg()["sl_atr_mult"]) / current_rate
        return -max(0.003, min(sl_dist, 0.08))

    def custom_exit(self, pair: str, trade: Trade, current_time,
                    current_rate: float, current_profit: float, **kwargs):
        held_h = (current_time - trade.open_date_utc).total_seconds() / 3600
        if held_h >= self._cfg()["max_hold_hours"]:
            return "time_max_exit"
        return None
