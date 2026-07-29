from __future__ import annotations

import os

from pandas import DataFrame

from RevoAdaptiveBacktestExitThesis import RevoAdaptiveBacktestExitThesis


class RevoAdaptiveBacktestCandleConfirm(RevoAdaptiveBacktestExitThesis):
    """A/B test bullish candlestick confirmation on proven Revo baseline."""

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        df = super().populate_indicators(dataframe, metadata)
        body = (df["close"] - df["open"]).abs()
        candle_range = (df["high"] - df["low"]).clip(lower=1e-12)
        lower_wick = df[["open", "close"]].min(axis=1) - df["low"]
        prev_body = (df["close"].shift(1) - df["open"].shift(1)).abs()

        df["bullish_pinbar"] = (
            (df["close"] > df["open"])
            & (lower_wick >= 1.5 * body)
            & (lower_wick / candle_range >= 0.5)
        ).astype(int)
        df["bullish_engulfing"] = (
            (df["close"].shift(1) < df["open"].shift(1))
            & (df["close"] > df["open"])
            & (df["open"] <= df["close"].shift(1))
            & (df["close"] >= df["open"].shift(1))
            & (body >= 1.5 * prev_body)
        ).astype(int)
        df["bullish_candle_confirm"] = (
            (df["bullish_pinbar"] == 1) | (df["bullish_engulfing"] == 1)
        ).astype(int)
        return df

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        df = super().populate_entry_trend(dataframe, metadata)
        mode = os.environ.get("REVO_CANDLE_CONFIRM_MODE", "none").strip().lower()
        if mode == "confirm":
            df["enter_long"] = ((df["enter_long"] == 1) & (df["bullish_candle_confirm"] == 1)).astype(int)
            df.loc[df["enter_long"] == 1, "enter_tag"] = "candle:confirm"
        return df
