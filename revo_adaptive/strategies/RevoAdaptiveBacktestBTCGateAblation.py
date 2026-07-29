from __future__ import annotations

import os

import numpy as np
import pandas as pd
from pandas import DataFrame

from RevoAdaptiveStrategy import RevoAdaptiveStrategy

BTC_PAIR = "BTC/USDT:USDT"


class RevoAdaptiveBacktestBTCGateAblation(RevoAdaptiveStrategy):
    """Flow-gate ablation + BTC 15m daily-anchored VWAP regime filter.

    Modes via REVO_FLOW_GATE_MODE:
      pure / scoring / block_danger / hard  (as before)
    BTC regime:
      long allowed only when BTC > daily-VWAP (regime == +1)
      short allowed only when BTC < daily-VWAP (regime == -1)
    NO LOOKAHEAD: 15m bar regime stamped at bar CLOSE, merged backward.
    """

    can_short = True

    def _btc_regime(self) -> DataFrame:
        btc = self.dp.get_pair_dataframe(BTC_PAIR, self.timeframe)
        if btc is None or getattr(btc, "empty", True):
            return pd.DataFrame(columns=["date", "btc_regime"])
        b = btc[["date", "high", "low", "close", "volume"]].copy()
        b["date"] = pd.to_datetime(b["date"], utc=True)
        b = b.set_index("date").sort_index()
        # 5m -> 15m
        agg = b.resample("15min").agg(
            {"high": "max", "low": "min", "close": "last", "volume": "sum"}
        ).dropna()
        if agg.empty:
            return pd.DataFrame(columns=["date", "btc_regime"])
        tp = (agg["high"] + agg["low"] + agg["close"]) / 3.0
        pv = tp * agg["volume"]
        day = agg.index.normalize()
        cum_pv = pv.groupby(day).cumsum()
        cum_v = agg["volume"].groupby(day).cumsum().replace(0, np.nan)
        vwap = cum_pv / cum_v
        regime = np.sign(agg["close"] - vwap).fillna(0).astype(int)
        # stamp at bar CLOSE (open + 15min) to prevent lookahead
        avail = agg.index + pd.Timedelta(minutes=15)
        return pd.DataFrame({"date": avail, "btc_regime": regime.values}).reset_index(drop=True)

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        df = super().populate_indicators(dataframe, metadata)
        # BTC regime merge (no-lookahead)
        reg = self._btc_regime()
        if reg.empty:
            df["btc_regime"] = 0
        else:
            left = df.copy()
            left["date"] = pd.to_datetime(left["date"], utc=True).astype("datetime64[ns, UTC]")
            reg = reg.copy()
            reg["date"] = pd.to_datetime(reg["date"], utc=True).astype("datetime64[ns, UTC]")
            merged = pd.merge_asof(
                left[["date"]].sort_values("date"),
                reg.sort_values("date"),
                on="date",
                direction="backward",
            )
            df["btc_regime"] = merged["btc_regime"].fillna(0).astype(int).values

        # Proxy flow injection (same as GateAblation)
        cvd = df["cvd_proxy"].astype(float)
        cvd_mu = cvd.rolling(48, min_periods=12).mean()
        cvd_sd = cvd.rolling(48, min_periods=12).std().replace(0, np.nan)
        cvd_z = ((cvd - cvd_mu) / cvd_sd).replace([np.inf, -np.inf], np.nan).fillna(0.0)
        vol_z = df["vol_z_proxy"].astype(float)
        long_ok = (cvd_z > 0.25) & (vol_z >= 0.8)
        hostile = (cvd_z < -0.75) | ((cvd_z < -0.25) & (vol_z >= 1.2))
        df["real_flow_available"] = 1
        df["real_cvd_z"] = cvd_z
        df["real_vol_z"] = vol_z
        df["real_flow_long"] = long_ok.astype(int)
        df["real_flow_hostile"] = hostile.astype(int)
        df["real_oi_delta"] = 0.0
        df["real_funding_z"] = 0.0
        df["real_funding_rate"] = 0.0
        df["funding_ok"] = 1
        df["funding_crowded"] = 0
        df["oi_ok"] = 1
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
        dataframe["enter_short"] = 0
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
        else:
            flow_guard = True
            mode = "pure"

        # BTC regime gate: long only when btc_regime > 0, short only when < 0
        btc_long = dataframe["btc_regime"] > 0
        btc_short = dataframe["btc_regime"] < 0

        cond_long = (
            (dataframe["liq_ok"] == 1)
            & (eff_score >= min_score)
            & (dataframe["rsi_ok"] == 1)
            & (dataframe["atr_explosive"] == 0)
            & (dataframe["not_falling_knife"] == 1)
            & flow_guard
            & btc_long
        )
        cond_short = (
            (dataframe["liq_ok"] == 1)
            & (eff_score >= min_score)
            & (dataframe["rsi_ok"] == 1)
            & (dataframe["atr_explosive"] == 0)
            & (dataframe["not_falling_knife"] == 1)
            & flow_guard
            & btc_short
        )
        dataframe.loc[cond_long, "enter_long"] = 1
        dataframe.loc[cond_short, "enter_short"] = 1
        dataframe.loc[cond_long | cond_short, "enter_tag"] = f"btc_ablation:{mode}"
        return dataframe