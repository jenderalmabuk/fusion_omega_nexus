from __future__ import annotations

import os

from pandas import DataFrame

from RevoAdaptiveBacktestExitThesis import RevoAdaptiveBacktestExitThesis


class RevoAdaptiveBacktestMarketStructure(RevoAdaptiveBacktestExitThesis):
    """No-lookahead market-structure label and gate ablation."""

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        df = super().populate_indicators(dataframe, metadata)
        df["ema21"] = df["close"].ewm(span=21, adjust=False).mean()
        df["sma50"] = df["close"].rolling(50, min_periods=50).mean()
        df["ema21_slope_pct"] = (df["ema21"] / df["ema21"].shift(12) - 1) * 100

        high24 = df["high"].rolling(24, min_periods=24).max()
        low24 = df["low"].rolling(24, min_periods=24).min()
        higher = (high24 > high24.shift(24)) & (low24 > low24.shift(24))
        lower = (high24 < high24.shift(24)) & (low24 < low24.shift(24))

        direction = df["close"].diff().gt(0).astype(int)
        df["direction_changes_24"] = direction.ne(direction.shift(1)).rolling(24).sum()
        above = df["ema21"] > df["sma50"]
        df["ma_crosses_24"] = above.ne(above.shift(1)).rolling(24).sum()

        df["regime_trending_up"] = (
            higher & (df["ema21"] > df["sma50"]) & (df["ema21_slope_pct"] > 0.15)
        ).astype(int)
        df["regime_trending_down"] = (
            lower & (df["ema21"] < df["sma50"]) & (df["ema21_slope_pct"] < -0.15)
        ).astype(int)
        df["regime_choppy"] = (
            (df["er48"] <= 0.15)
            & ((df["atr_pct"] > 4.0) | (df["direction_changes_24"] >= 15) | (df["ma_crosses_24"] >= 3))
        ).astype(int)
        df["regime_ranging"] = (
            (df["ema21_slope_pct"].abs() <= 0.15)
            & (df["er48"] <= 0.15)
            & (df["atr_pct"] <= 4.0)
            & (df["regime_choppy"] == 0)
        ).astype(int)
        return df

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        c = self._cfg()
        mode = os.environ.get("REVO_STRUCTURE_MODE", "baseline").strip().lower()
        score = dataframe["entry_score"]
        discount_max = float(os.environ.get("REVO_TEST_DISCOUNT_MAX", str(c["discount_max"])))
        rsi_max = float(os.environ.get("REVO_TEST_RSI_MAX", str(c["rsi_max"])))
        min_score = float(os.environ.get("REVO_TEST_MIN_SCORE", str(c["min_score"])))
        atr_max = float(os.environ.get("REVO_TEST_ATR_MAX", str(c["atr_max"])))
        if mode == "bonus":
            score = score + ((dataframe["regime_ranging"] == 1) | (dataframe["regime_trending_up"] == 1)).astype(int)
        elif mode == "bonus_ranging":
            score = score + dataframe["regime_ranging"]
        elif mode == "bonus_up":
            score = score + dataframe["regime_trending_up"]

        cond = (
            (dataframe["liq_ok"] == 1)
            & (score >= min_score)
            & (dataframe["rsi"] <= rsi_max)
            & (dataframe["atr_pct"] <= atr_max)
            & (dataframe["dist_ema55_pct"] >= -discount_max)
        )
        if mode == "no_choppy":
            cond &= dataframe["regime_choppy"] == 0
        elif mode == "ranging":
            cond &= dataframe["regime_ranging"] == 1
        elif mode == "ranging_up":
            cond &= (dataframe["regime_ranging"] == 1) | (dataframe["regime_trending_up"] == 1)

        dataframe["enter_long"] = 0
        dataframe.loc[cond, "enter_long"] = 1
        dataframe.loc[cond, "enter_tag"] = f"structure:{mode}"
        return dataframe
