from __future__ import annotations

import json
import os
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
    use_custom_stoploss = True
    minimal_roi = {}
    stoploss = -0.08
    _funnel_path = Path("/freqtrade/user_data/local/revo_auditable_funnel.jsonl")
    _funnel_seen: set[tuple[str, str]] = set()

    @classmethod
    def _audit_latest_gate(cls, pair: str, idx, gate: GateInput, decision) -> None:
        candle = pd.Timestamp(idx).isoformat()
        key = (pair, candle)
        if key in cls._funnel_seen:
            return
        cls._funnel_seen.add(key)
        row = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "candle": candle,
            "pair": pair,
            "allow": decision.allow,
            "reasons": list(decision.reasons),
            "score": gate.score,
            "discount_pct": gate.discount_pct,
            "rsi": gate.rsi,
            "atr_pct": gate.atr_pct,
            "er": gate.er,
            "qvol_med48": gate.qvol_med48,
            "flow": gate.flow,
            "btc_mode": gate.btc_mode,
            "data_age_sec": gate.data_age_sec,
        }
        cls._funnel_path.parent.mkdir(parents=True, exist_ok=True)
        with cls._funnel_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")

    def _core_config(self) -> Config:
        c = self._cfg()
        return Config(
            min_score=int(c["min_score"]),
            min_discount_pct=float(c["discount"]),
            rsi_max=float(c["rsi_max"]),
            min_qvol_med48=float(c["min_qvol"]),
            max_atr_pct=float(c["atr_max"]),
            max_er=float(os.environ.get("REVO_ER_CHOP_MAX", "0.15")),
            max_discount_pct=float(os.environ.get("REVO_ENTRY_DISCOUNT_MAX_PCT", "6")),
            max_data_age_sec=int(float(os.environ.get("REVO_FLOW_MAX_AGE_SEC", "660"))),
        )

    @staticmethod
    def _row_gate_input(pair: str, row: pd.Series) -> GateInput:
        discount = max(0.0, -float(row.get("dist_ema55_pct", 0.0)))
        flow = "long" if int(row.get("real_flow_long", 0)) else "unknown"
        if int(row.get("real_flow_hostile", 0)):
            flow = "hostile"
        age = 0 if int(row.get("real_flow_available", 0)) == 0 else 1

        # Real BTC regime from dataframe
        btc_mode = "neutral"
        btc_coupling = "coupled"
        btc_mode_raw = row.get("btc_regime_mode", "")
        if isinstance(btc_mode_raw, str) and btc_mode_raw:
            btc_mode = btc_mode_raw
        btc_coupling_raw = row.get("btc_coupling", "")
        if isinstance(btc_coupling_raw, str) and btc_coupling_raw:
            btc_coupling = btc_coupling_raw

        # Real data age from flow context if available
        flow_age_raw = row.get("flow_age_sec", 0)
        data_age_sec = age if age > 0 else int(flow_age_raw) if flow_age_raw is not None else 0

        return GateInput(
            symbol=pair,
            score=float(row.get("entry_score", 0.0)),
            rsi=float(row.get("rsi", 100.0)),
            discount_pct=discount,
            qvol_med48=float(row.get("qvol_5m_med48", row.get("qvol_5m", 0.0))),
            atr_pct=float(row.get("atr_pct", 999.0)),
            er=float(row.get("er48", 999.0)),
            flow=flow,
            btc_mode=btc_mode,
            btc_coupling=btc_coupling,
            data_age_sec=data_age_sec,
        )

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        cfg = self._core_config()
        pair = metadata.get("pair", "")
        dataframe["enter_long"] = 0
        dataframe["enter_tag"] = None
        if dataframe.empty:
            return dataframe
        last_idx = dataframe.index[-1]
        for idx, row in dataframe.iterrows():
            gate = self._row_gate_input(pair, row)
            decision = decide_entry(gate, cfg)
            if idx == last_idx:
                self._audit_latest_gate(pair, idx, gate, decision)
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
        exit_mode = os.environ.get("REVO_EXIT_MODE", "time_max")
        max_hold_hours = float(os.environ.get("REVO_MAX_HOLD_HOURS", "4"))
        age_hours = (current_time.replace(tzinfo=timezone.utc) - trade.open_date_utc.replace(tzinfo=timezone.utc)).total_seconds() / 3600.0
        if exit_mode == "time_max" and age_hours >= max_hold_hours:
            return "time_max_exit"

        age_min = max(0.0, age_hours * 60.0)
        entry = float(trade.open_rate)
        max_rate = float(getattr(trade, "max_rate", current_rate) or current_rate)
        min_rate = float(getattr(trade, "min_rate", current_rate) or current_rate)
        mfe = (max_rate / entry - 1.0) * 100.0
        mae = (min_rate / entry - 1.0) * 100.0
        decision = decide_exit(Position(pair, "long", entry, age_min, mfe, mae, False, True, current_profit * 100.0), self._core_config())
        return decision.reason.lower() if decision.exit else None

    def custom_stoploss(
        self,
        pair: str,
        trade: Trade,
        current_time,
        current_rate: float,
        current_profit: float,
        **kwargs,
    ) -> float:
        """ATR3 stop fixed from entry; Freqtrade expects ratio to current price."""
        atr14 = 0.0
        try:
            df = self.dp.get_pair_dataframe(pair, self.timeframe)
            if df is not None and not df.empty and "atr" in df.columns:
                atr14 = float(df["atr"].iloc[-1])
        except Exception:
            pass
        if atr14 <= 0:
            return -0.02
        stop_price = float(trade.open_rate) - (atr14 * 3.0)
        relative = (stop_price / current_rate) - 1.0
        return max(-0.08, min(-0.003, relative))