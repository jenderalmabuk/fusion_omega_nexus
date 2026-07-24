from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import pandas as pd
from pandas import DataFrame
from freqtrade.persistence import Trade

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from auditable_core.core import Config, GateInput, Position, decide_entry, decide_exit
from RevoAdaptiveStrategy import RevoAdaptiveStrategy


class RevoAuditableCoreStrategy(RevoAdaptiveStrategy):
    """Freqtrade adapter for the small auditable decision core.

    Safe-by-default: new strategy class only; does not change the running
    RevoAdaptiveStrategy container unless explicitly selected in config/backtest.
    """

    use_exit_signal = True

    def _core_config(self) -> Config:
        c = self._cfg()
        return Config(
            min_score=int(c["min_score"]),
            min_discount_pct=float(c["discount"]),
            rsi_max=float(c["rsi_max"]),
            min_qvol_med48=float(c["min_qvol"]),
            max_atr_pct=float(c["atr_max"]),
            max_data_age_sec=int(float(__import__("os").environ.get("REVO_FLOW_MAX_AGE_SEC", "660"))),
        )

    @staticmethod
    def _row_gate_input(pair: str, row: pd.Series) -> GateInput:
        discount = max(0.0, -float(row.get("dist_ema55_pct", 0.0)))
        flow = "long" if int(row.get("real_flow_long", 0)) else "unknown"
        if int(row.get("real_flow_hostile", 0)):
            flow = "hostile"
        age = 0 if int(row.get("real_flow_available", 0)) == 0 else 1
        return GateInput(
            symbol=pair,
            score=float(row.get("entry_score", 0.0)),
            rsi=float(row.get("rsi", 100.0)),
            discount_pct=discount,
            qvol_med48=float(row.get("qvol_5m_med48", row.get("qvol_5m", 0.0))),
            atr_pct=float(row.get("atr_pct", 999.0)),
            flow=flow,
            btc_mode="neutral",
            btc_coupling="coupled",
            data_age_sec=age,
        )

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        cfg = self._core_config()
        pair = metadata.get("pair", "")
        dataframe["enter_long"] = 0
        dataframe["enter_tag"] = None
        if dataframe.empty:
            return dataframe
        for idx, row in dataframe.iterrows():
            decision = decide_entry(self._row_gate_input(pair, row), cfg)
            if decision.allow:
                dataframe.at[idx, "enter_long"] = 1
                dataframe.at[idx, "enter_tag"] = "auditable_core:" + "+".join(decision.reasons)
        return dataframe

    def custom_exit(
        self,
        pair: str,
        trade: Trade,
        current_time: datetime,
        current_rate: float,
        current_profit: float,
        **kwargs,
    ) -> Optional[str]:
        age_min = max(0.0, (current_time.replace(tzinfo=timezone.utc) - trade.open_date_utc.replace(tzinfo=timezone.utc)).total_seconds() / 60.0)
        entry = float(trade.open_rate)
        max_rate = float(getattr(trade, "max_rate", current_rate) or current_rate)
        min_rate = float(getattr(trade, "min_rate", current_rate) or current_rate)
        mfe = (max_rate / entry - 1.0) * 100.0
        mae = (min_rate / entry - 1.0) * 100.0
        decision = decide_exit(Position(pair, "long", entry, age_min, mfe, mae, False, True, current_profit * 100.0), self._core_config())
        return decision.reason.lower() if decision.exit else None
