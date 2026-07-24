# =============================================================================
# MomoBreakout — BASELINE genre-test: momentum / breakout continuation.
#
# TUJUAN: menjawab pertanyaan paling mendasar — apakah 5 build reversion (semua
# PF<1) gagal karena EKSEKUSI, atau karena GENRE-nya salah untuk crypto 5m?
# Genre ini struktural berlawanan dgn reversion:
#   - Reversion: beli murah di zona, target fixed 2R  -> mati krn TP-hit<33%.
#   - Momentum : beli KEKUATAN (breakout), biarkan winner LARI (trailing).
# Payoff fat-tail (sedikit menang besar, banyak rugi kecil) JUSTRU selamat dari
# win-rate rendah — kebalikan dari tembok 2R.
#
# DESAIN MINIMAL (baseline, bukan optimasi):
#   - Entry : Donchian breakout — close tembus high/low N-bar SEBELUMNYA.
#   - Filter: tren H1 (EMA50/200) — breakout hanya searah tren besar.
#   - Exit  : Chandelier trailing stop (ratcheting) — highest_high - mult*ATR.
#             Ini initial-stop SEKALIGUS trailing. TIDAK ADA fixed TP (itu poinnya).
#
# ANTI-LOOKAHEAD: Donchian di-shift 1 (buang bar berjalan); H1 resample+shift 1;
# Chandelier & ATR hanya pakai bar tertutup. Diuji kausalitas terpisah.
# =============================================================================
from datetime import datetime

import numpy as np
import pandas as pd
import talib.abstract as ta
from pandas import DataFrame

from freqtrade.strategy import IStrategy, stoploss_from_absolute
from freqtrade.persistence import Trade


def resample_htf(df: DataFrame, minutes: int) -> DataFrame:
    d = df.copy().set_index("date")
    agg = {"open": "first", "high": "max", "low": "min",
           "close": "last", "volume": "sum"}
    return d.resample(f"{minutes}min", label="right",
                      closed="right").agg(agg).dropna()


class MomoBreakout(IStrategy):
    INTERFACE_VERSION = 3
    timeframe = "5m"
    can_short = True

    minimal_roi = {"0": 100.0}     # ROI off — exit murni via chandelier trailing
    stoploss = -0.99               # fallback; custom_stoploss yang bekerja
    use_custom_stoploss = True
    process_only_new_candles = True
    use_exit_signal = False        # exit hanya via trailing stop (biarkan lari)
    exit_profit_only = False

    startup_candle_count: int = 2400   # EMA200 H1 = 200*12 bar 5m

    # ---- params baseline (klasik turtle-style, TIDAK dituning) ----
    donchian = 20          # lookback breakout (bar 5m)
    atr_period = 14
    chand_period = 22      # lookback chandelier
    chand_mult = 3.0       # ATR multiple chandelier (klasik)
    use_h1_trend = True
    ema_fast = 50
    ema_slow = 200

    def leverage(self, pair, current_time, current_rate, proposed_leverage,
                 max_leverage, side, **kwargs) -> float:
        return 1.0

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        df = dataframe.copy()

        # ===== Filter tren H1 (resample dari 5m, shift 1 = closed) =====
        if self.use_h1_trend:
            h1 = resample_htf(df, 60)
            h1["ema_f"] = ta.EMA(h1, timeperiod=self.ema_fast)
            h1["ema_s"] = ta.EMA(h1, timeperiod=self.ema_slow)
            h1["bull"] = (h1["ema_f"] > h1["ema_s"]) & (h1["close"] > h1["ema_s"])
            h1["bear"] = (h1["ema_f"] < h1["ema_s"]) & (h1["close"] < h1["ema_s"])
            h1s = h1[["bull", "bear"]].shift(1)
            h1s.columns = ["h1_bull", "h1_bear"]
            df = pd.merge_asof(df.sort_values("date"),
                               h1s.reset_index().sort_values("date"),
                               on="date", direction="backward")
            df["h1_bull"] = df["h1_bull"].fillna(False).astype(bool)
            df["h1_bear"] = df["h1_bear"].fillna(False).astype(bool)
        else:
            df["h1_bull"] = True
            df["h1_bear"] = True

        # ===== Indikator M5 =====
        df["atr"] = ta.ATR(df, timeperiod=self.atr_period)

        # Donchian channel — shift 1: high/low N-bar SEBELUM bar berjalan (causal)
        df["dc_high"] = df["high"].rolling(self.donchian).max().shift(1)
        df["dc_low"] = df["low"].rolling(self.donchian).min().shift(1)

        # Chandelier trailing levels (dipakai di custom_stoploss)
        df["chand_long"] = (df["high"].rolling(self.chand_period).max()
                            - self.chand_mult * df["atr"])
        df["chand_short"] = (df["low"].rolling(self.chand_period).min()
                             + self.chand_mult * df["atr"])
        return df

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        df = dataframe
        long_c = ((df["close"] > df["dc_high"]) & df["h1_bull"] & (df["atr"] > 0))
        short_c = ((df["close"] < df["dc_low"]) & df["h1_bear"] & (df["atr"] > 0))
        df["enter_long"] = 0
        df["enter_short"] = 0
        df.loc[long_c, "enter_long"] = 1
        df.loc[short_c, "enter_short"] = 1
        df.loc[long_c, "enter_tag"] = "momo_long"
        df.loc[short_c, "enter_tag"] = "momo_short"
        return df

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["exit_long"] = 0
        dataframe["exit_short"] = 0
        return dataframe

    def custom_stoploss(self, pair: str, trade: Trade, current_time: datetime,
                        current_rate: float, current_profit: float, **kwargs):
        df, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        if df is None or len(df) == 0:
            return None
        rows = df.loc[df["date"] <= current_time]
        if len(rows) == 0:
            return None
        row = rows.iloc[-1]
        if trade.is_short:
            lvl = row["chand_short"]
            if pd.isna(lvl):
                return None
            best = trade.get_custom_data("sl")
            best = float(lvl) if best is None else min(float(best), float(lvl))  # ratchet turun
            trade.set_custom_data("sl", best)
            return stoploss_from_absolute(best, current_rate, is_short=True,
                                          leverage=trade.leverage)
        else:
            lvl = row["chand_long"]
            if pd.isna(lvl):
                return None
            best = trade.get_custom_data("sl")
            best = float(lvl) if best is None else max(float(best), float(lvl))  # ratchet naik
            trade.set_custom_data("sl", best)
            return stoploss_from_absolute(best, current_rate, is_short=False,
                                          leverage=trade.leverage)
