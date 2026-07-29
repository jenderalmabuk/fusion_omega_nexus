from __future__ import annotations

import os

import numpy as np
from pandas import DataFrame

from RevoAdaptiveStrategy import RevoAdaptiveStrategy


class RevoAdaptiveBacktestGateAblation(RevoAdaptiveStrategy):
    """Vectorized backtest of flow-gate modes with OHLCV proxy flow.

    Pure OHLCV has no real flow JSON, so live modes collapse (real_flow_available=0).
    This class injects a no-lookahead CVD/volume proxy so pure / scoring / block_danger
    actually diverge.

    Modes via REVO_FLOW_GATE_MODE:
      pure         — long MR score gates only; no flow veto/score
      scoring      — flow long +2 / hostile -2, min_score+1; veto cvd_z < -1.5
      block_danger — no score change; veto only cvd_z < -1.5
      hard         — require flow long (proxy)
    """

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        df = super().populate_indicators(dataframe, metadata)
        # No-lookahead proxy: completed-bar CVD/vol z from price×vol (same series as parent proxies)
        cvd = df["cvd_proxy"].astype(float)
        cvd_mu = cvd.rolling(48, min_periods=12).mean()
        cvd_sd = cvd.rolling(48, min_periods=12).std().replace(0, np.nan)
        cvd_z = ((cvd - cvd_mu) / cvd_sd).replace([np.inf, -np.inf], np.nan).fillna(0.0)
        vol_z = df["vol_z_proxy"].astype(float)
        # Direction: positive CVD z + non-weak volume = long-friendly; strong negative = hostile
        long_ok = (cvd_z > 0.25) & (vol_z >= 0.8)
        hostile = (cvd_z < -0.75) | ((cvd_z < -0.25) & (vol_z >= 1.2))
        df["real_flow_available"] = 1
        df["real_cvd_z"] = cvd_z
        df["real_vol_z"] = vol_z
        df["real_flow_long"] = long_ok.astype(int)
        df["real_flow_hostile"] = hostile.astype(int)
        # Keep funding/oi neutral in pure OHLCV ablation
        df["real_oi_delta"] = 0.0
        df["real_funding_z"] = 0.0
        df["real_funding_rate"] = 0.0
        df["funding_ok"] = 1
        df["funding_crowded"] = 0
        df["oi_ok"] = 1
        # Rebuild vol/cvd ok + score with injected flow (parent used available=0 earlier)
        df["vol_ok"] = (df["real_vol_z"] >= -0.5).astype(int)
        df["cvd_ok"] = (df["real_cvd_z"] > -0.5).astype(int)
        c = self._cfg()
        df["entry_score"] = (
            df["at_discount"] * 2
            + df["rsi_ok"]
            + df["cvd_ok"] * 2
            + df["oi_ok"]
            + df["funding_ok"] * 2
            + df["pair_uptrend_pullback"]
            + df["btc_ok"]
            + df["vol_ok"]
            - df["er_chop"]
            - df["btc_dump"]
            - df["atr_explosive"]
            - df["funding_crowded"] * 2
        ).astype(int)
        return df

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        c = self._cfg()
        mode = str(os.environ.get("REVO_FLOW_GATE_MODE", c["flow_gate_mode"])).strip().lower()
        dataframe["enter_long"] = 0
        min_score = c["min_score"]
        eff_score = dataframe["entry_score"]
        if mode == "scoring":
            eff_score = (
                dataframe["entry_score"]
                + dataframe["real_flow_long"] * 2
                - dataframe["real_flow_hostile"] * 2
            )
            min_score = c["min_score"] + 1
        if mode in ("scoring", "block_danger"):
            flow_guard = dataframe["real_cvd_z"] >= -1.5
        elif mode == "hard":
            flow_guard = dataframe["real_flow_long"] == 1
        else:  # pure / off / none
            flow_guard = True
            mode = "pure"
        cond = (
            (dataframe["liq_ok"] == 1)
            & (eff_score >= min_score)
            & (dataframe["rsi_ok"] == 1)
            & (dataframe["atr_explosive"] == 0)
            & (dataframe["not_falling_knife"] == 1)
            & flow_guard
        )
        dataframe.loc[cond, "enter_long"] = 1
        dataframe.loc[cond, "enter_tag"] = f"gate_ablation:{mode}"
        return dataframe
