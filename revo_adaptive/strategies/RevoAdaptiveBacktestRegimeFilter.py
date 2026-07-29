from __future__ import annotations

import os

import numpy as np
import pandas as pd
from pandas import DataFrame

from freqtrade.persistence import Trade
from RevoAdaptiveStrategy import RevoAdaptiveStrategy


class RevoAdaptiveBacktestRegimeFilter(RevoAdaptiveStrategy):
    """Regime filter ablation: none / btc_4h / breadth / combo + SL modes.

    Regime modes via REVO_REGIME_MODE:
      none      — no filter (baseline)
      btc_4h    — long only when BTC 4h > EMA200 (no lookahead: regime stamped at bar close)
      breadth   — long only when % pairs above 4h EMA200 > 50%
      combo     — both BTC 4h bull AND breadth > 50%

    SL modes via REVO_SL_MODE: fixed / atr2 / atr3 (best from prior ablation)
    """

    use_custom_stoploss = True
    process_only_new_candles = True

    def _cfg(self):
        c = super()._cfg()
        c["regime_mode"] = os.environ.get("REVO_REGIME_MODE", "none").strip().lower()
        c["sl_mode"] = os.environ.get("REVO_SL_MODE", "fixed").strip().lower()
        c["sl_atr_mult"] = float(os.environ.get("REVO_SL_ATR_MULT", "0"))
        return c

    def _btc_4h_bull(self) -> DataFrame:
        """BTC 4h EMA200 regime, no lookahead: regime stamped at bar CLOSE."""
        btc = self.dp.get_pair_dataframe("BTC/USDT:USDT", self.timeframe)
        if btc is None or getattr(btc, "empty", True):
            return pd.DataFrame(columns=["date", "btc_4h_bull"])
        b = btc[["date", "close"]].copy()
        b["date"] = pd.to_datetime(b["date"], utc=True)
        b = b.set_index("date").sort_index()
        # 5m -> 4h
        df_4h = b.resample("4h").last().dropna()
        if len(df_4h) < 200:
            return pd.DataFrame(columns=["date", "btc_4h_bull"])
        df_4h["ema200"] = df_4h["close"].ewm(span=200, min_periods=50).mean()
        df_4h["bull"] = (df_4h["close"] > df_4h["ema200"]).astype(int)
        # Regime available at bar CLOSE (index + 4h) to prevent lookahead
        df_4h["avail"] = df_4h.index + pd.Timedelta(hours=4)
        out = pd.DataFrame({"date": df_4h["avail"], "btc_4h_bull": df_4h["bull"].values})
        return out

    def _breadth_4h(self) -> DataFrame:
        """Market breadth: % pairs above 4h EMA200, no lookahead."""
        import glob
        from pathlib import Path

        data_dir = Path("/freqtrade/user_data/data/bybit/futures")
        pair_files = list(data_dir.glob("*_5m-futures.feather"))
        if len(pair_files) < 20:
            return pd.DataFrame(columns=["date", "breadth_ok"])

        np.random.seed(42)
        sample_files = np.random.choice(pair_files, min(50, len(pair_files)), replace=False)

        breadth_map = {}
        for pf in sample_files:
            try:
                df = pd.read_feather(pf)
                df["date"] = pd.to_datetime(df["date"], utc=True)
                df = df.set_index("date").sort_index()
                df_4h = df.resample("4h").last().dropna()
                if len(df_4h) < 200:
                    continue
                df_4h["ema200"] = df_4h["close"].ewm(span=200, min_periods=50).mean()
                df_4h["above"] = (df_4h["close"] > df_4h["ema200"]).astype(int)
                df_4h["avail"] = df_4h.index + pd.Timedelta(hours=4)
                for idx, row in df_4h.iterrows():
                    dt = row["avail"]
                    if dt not in breadth_map:
                        breadth_map[dt] = {"above": 0, "total": 0}
                    breadth_map[dt]["above"] += row["above"]
                    breadth_map[dt]["total"] += 1
            except Exception:
                continue

        if not breadth_map:
            return pd.DataFrame(columns=["date", "breadth_ok"])

        records = []
        for dt, vals in breadth_map.items():
            if vals["total"] >= 10:
                pct = vals["above"] / vals["total"]
                records.append({"date": dt, "breadth_ok": 1 if pct > 0.5 else 0})

        return pd.DataFrame(records).sort_values("date").reset_index(drop=True)

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        df = super().populate_indicators(dataframe, metadata)

        # ATR for custom stoploss
        if "atr" not in df.columns:
            df["atr"] = self._atr(df, 14)

        # BTC 4h regime
        btc_regime = self._btc_4h_bull()
        if not btc_regime.empty:
            left = df.copy()
            left["date"] = pd.to_datetime(left["date"], utc=True)
            # merge_asof requires sorted unique keys
            left_sorted = left[["date"]].sort_values("date").reset_index(drop=True)
            btc_sorted = btc_regime.sort_values("date").reset_index(drop=True)
            merged = pd.merge_asof(
                left_sorted,
                btc_sorted,
                on="date",
                direction="backward",
            )
            df["btc_4h_bull"] = merged["btc_4h_bull"].fillna(0).astype(int).values
        else:
            df["btc_4h_bull"] = 0

        # Breadth regime
        breadth = self._breadth_4h()
        if not breadth.empty:
            left = df.copy()
            left["date"] = pd.to_datetime(left["date"], utc=True)
            left_sorted = left[["date"]].sort_values("date").reset_index(drop=True)
            br_sorted = breadth.sort_values("date").reset_index(drop=True)
            merged = pd.merge_asof(
                left_sorted,
                br_sorted,
                on="date",
                direction="backward",
            )
            df["breadth_ok"] = merged["breadth_ok"].fillna(0).astype(int).values
        else:
            df["breadth_ok"] = 0

        return df

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        c = self._cfg()
        regime_mode = c["regime_mode"]
        dataframe["enter_long"] = 0

        # Pure long-MR base condition
        base_cond = (
            (dataframe["liq_ok"] == 1)
            & (dataframe["entry_score"] >= c["min_score"])
            & (dataframe["rsi_ok"] == 1)
            & (dataframe["atr_explosive"] == 0)
            & (dataframe["not_falling_knife"] == 1)
        )

        # Regime filter
        if regime_mode == "btc_4h":
            regime_cond = dataframe["btc_4h_bull"] == 1
        elif regime_mode == "breadth":
            regime_cond = dataframe["breadth_ok"] == 1
        elif regime_mode == "combo":
            regime_cond = (dataframe["btc_4h_bull"] == 1) & (dataframe["breadth_ok"] == 1)
        else:  # none
            regime_cond = True

        cond = base_cond & regime_cond
        dataframe.loc[cond, "enter_long"] = 1
        dataframe.loc[cond, "enter_tag"] = f"regime:{regime_mode}"
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
        c = self._cfg()
        mode = c["sl_mode"]
        mult = c["sl_atr_mult"]
        if mode == "fixed" or mult <= 0:
            return -0.02
        atr = self._get_atr14(pair)
        if atr <= 0:
            return -0.02
        sl_dist = (atr * mult) / current_rate
        return -max(0.003, min(sl_dist, 0.08))