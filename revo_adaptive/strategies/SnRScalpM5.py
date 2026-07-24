# =============================================================================
# SnRScalpM5 — Mekanisasi strategi video Forex Sarjana
# "Teknik Scalping M5, Konfirmasi Entri Support & Resistance"
# https://www.youtube.com/watch?v=P1iPIf58-Mc
#
# Tujuan: BACKTEST JUJUR (fee + slippage nyata). BUKAN untuk live dulu.
# Sumber data: bybit futures 5m. HTF 1h DITURUNKAN dari resample 5m,
# di-shift 1 bar HTF => tidak ada lookahead / repaint.
#
# Aturan (verbatim dari video, dimekanisasi):
#   1. Trend filter di HTF (1h): EMA50 vs EMA200.
#        uptrend  => hanya BUY, cari area Support.
#        downtrend=> hanya SELL, cari area Resistance.
#   2. Area S&R di HTF: rolling swing low (support) / swing high (resistance)
#        atas N candle HTF yang SUDAH CLOSE (donchian, non-repaint).
#   3. Trigger entry di LTF (5m):
#        BUY : harga sentuh zona Support HTF -> Bullish Engulfing ->
#              WAJIB 1 candle konfirmasi hijau close di atas close bearish sebelumnya.
#        SELL: harga sentuh zona Resistance HTF -> Bearish Engulfing ->
#              WAJIB 1 candle konfirmasi merah.
#   4. Risiko (ATR-based):
#        SL BUY  = swing low LTF terdekat - ATR(entry).
#        SL SELL = swing high LTF terdekat + ATR(entry).
#        TP      = RR statis 1:2 (jarak TP = 2x jarak SL).
#   5. Filter invalidate (bot DILARANG entry):
#        - Engulfing menggantung: body engulfing tidak masuk zona S&R.
#        - Candle konfirmasi abnormal: range > k * ATR (terlalu besar).
# =============================================================================
from datetime import datetime
from typing import Optional

import numpy as np
import pandas as pd
import talib.abstract as ta
from pandas import DataFrame

from freqtrade.strategy import IStrategy, stoploss_from_absolute
from freqtrade.persistence import Trade


def resample_htf(df: DataFrame, minutes: int) -> DataFrame:
    """Resample 5m OHLCV -> HTF (minutes). Index by date, agg OHLCV."""
    d = df.copy()
    d = d.set_index("date")
    agg = {
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum",
    }
    rule = f"{minutes}min"
    htf = d.resample(rule, label="right", closed="right").agg(agg).dropna()
    return htf


class SnRScalpM5(IStrategy):
    INTERFACE_VERSION = 3

    timeframe = "5m"
    can_short = True

    # SL/TP dikelola penuh oleh custom_stoploss + custom_exit (ATR & RR 1:2).
    # ROI/stoploss statis dimatikan supaya tidak "mencuri" exit.
    minimal_roi = {"0": 100.0}
    stoploss = -0.99
    use_custom_stoploss = True

    process_only_new_candles = True
    use_exit_signal = True
    exit_profit_only = False
    ignore_roi_if_entry_signal = False

    # EMA200 di HTF 1h butuh 200 candle 1h = 200*12 candle 5m = 2400.
    startup_candle_count: int = 2400

    # ---- parameter strategi (bisa di-hyperopt nanti) ----
    htf_minutes = 60          # HTF = 1 jam
    ema_fast = 50
    ema_slow = 200
    sr_lookback = 20          # jumlah candle HTF utk swing high/low (area S&R)
    zone_tol = 0.003          # toleransi "sentuh" zona = 0.3%
    ltf_swing = 10            # swing low/high LTF utk SL (candle 5m)
    atr_period = 14
    rr = 2.0                  # risk:reward statis 1:2
    sl_atr_mult = 1.0         # lebar SL = N x ATR di luar swing (sweep-able)
    max_conf_atr = 2.5        # candle konfirmasi > 2.5*ATR => batal (abnormal)

    # ---- Confluence volume (Langkah 3 SMC): senjata utama lawan over-trading.
    # Mekanisasi icon 🚦 volSpike / ⚡ highVol dari Pine Script user. ----
    require_vol_confluence = True
    vol_sma_period = 20
    vol_spike_mult = 2.0      # volume > 2x SMA(vol) => proxy injeksi volume institusi
    high_vol_atr_mult = 1.5   # range candle > 1.5*ATR => volatilitas tinggi

    def leverage(self, pair, current_time, current_rate, proposed_leverage,
                 max_leverage, side, **kwargs) -> float:
        # Low risk: no leverage.
        return 1.0

    # ------------------------------------------------------------------ #
    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        df = dataframe.copy()

        # ===== HTF (1h) diturunkan dari 5m =====
        htf = resample_htf(df, self.htf_minutes)
        htf["ema_fast"] = ta.EMA(htf, timeperiod=self.ema_fast)
        htf["ema_slow"] = ta.EMA(htf, timeperiod=self.ema_slow)
        # Area S&R = swing high/low atas N candle HTF yang SUDAH CLOSE.
        htf["res"] = htf["high"].rolling(self.sr_lookback).max()
        htf["sup"] = htf["low"].rolling(self.sr_lookback).min()

        # SHIFT 1 bar HTF => tiap candle 5m hanya melihat candle HTF
        # sebelumnya yang sudah close. Ini yang mencegah lookahead/repaint.
        htf_shift = htf[["ema_fast", "ema_slow", "res", "sup"]].shift(1)
        htf_shift.columns = ["htf_ema_fast", "htf_ema_slow", "htf_res", "htf_sup"]

        # merge_asof: tiap baris 5m ambil nilai HTF terakhir yang <= date-nya.
        df = pd.merge_asof(
            df.sort_values("date"),
            htf_shift.reset_index().sort_values("date"),
            on="date",
            direction="backward",
        )

        # ===== LTF (5m) indikator =====
        df["atr"] = ta.ATR(df, timeperiod=self.atr_period)

        body = (df["close"] - df["open"]).abs()
        rng = (df["high"] - df["low"]).replace(0, np.nan)
        df["body"] = body
        df["rng"] = rng

        # ---- Volume confluence: mekanisasi 🚦 volSpike + ⚡ highVol dari Pine ----
        df["vol_sma"] = df["volume"].rolling(self.vol_sma_period).mean()
        df["vol_spike"] = df["volume"] > (self.vol_spike_mult * df["vol_sma"])
        df["high_vol"] = df["rng"] > (self.high_vol_atr_mult * df["atr"])
        df["vol_confluence"] = (df["vol_spike"] | df["high_vol"]).fillna(False)
        if not self.require_vol_confluence:
            df["vol_confluence"] = True

        df["green"] = df["close"] > df["open"]
        df["red"] = df["close"] < df["open"]

        # Engulfing (candle sebelumnya vs 2 sebelumnya)
        o1, c1 = df["open"].shift(1), df["close"].shift(1)
        o2, c2 = df["open"].shift(2), df["close"].shift(2)
        prev_bull = c1 > o1
        prev_bear = c1 < o1
        # bullish engulfing: candle-1 hijau menelan body candle-2 (yg bearish)
        df["bull_engulf"] = prev_bull & (c2 < o2) & (c1 >= o2) & (o1 <= c2)
        # bearish engulfing: candle-1 merah menelan body candle-2 (yg bullish)
        df["bear_engulf"] = prev_bear & (c2 > o2) & (c1 <= o2) & (o1 >= c2)

        # Swing LTF utk SL (low/high N candle terakhir yg sudah close)
        df["ltf_swing_low"] = df["low"].rolling(self.ltf_swing).min().shift(1)
        df["ltf_swing_high"] = df["high"].rolling(self.ltf_swing).max().shift(1)

        # Trend HTF
        df["uptrend"] = df["htf_ema_fast"] > df["htf_ema_slow"]
        df["downtrend"] = df["htf_ema_fast"] < df["htf_ema_slow"]

        # "Sentuh" zona: low candle-1 masuk zona support (utk long),
        # high candle-1 masuk zona resistance (utk short).
        low1 = df["low"].shift(1)
        high1 = df["high"].shift(1)
        df["touch_sup"] = low1 <= df["htf_sup"] * (1 + self.zone_tol)
        df["touch_res"] = high1 >= df["htf_res"] * (1 - self.zone_tol)

        # Invalidate: engulfing menggantung (body candle-1 tidak menyentuh zona)
        df["engulf_in_sup"] = c1 <= df["htf_sup"] * (1 + self.zone_tol)
        df["engulf_in_res"] = c1 >= df["htf_res"] * (1 - self.zone_tol)
        # Invalidate: candle konfirmasi (candle sekarang) abnormal besar
        df["conf_abnormal"] = df["rng"] > (self.max_conf_atr * df["atr"])

        # ===== Hitung SL & TP price di candle sinyal (RR 1:2, ATR-based) =====
        # LONG
        sl_long = df["ltf_swing_low"] - self.sl_atr_mult * df["atr"]
        risk_long = df["close"] - sl_long
        tp_long = df["close"] + self.rr * risk_long
        # SHORT
        sl_short = df["ltf_swing_high"] + self.sl_atr_mult * df["atr"]
        risk_short = sl_short - df["close"]
        tp_short = df["close"] - self.rr * risk_short

        df["sl_long"] = sl_long
        df["tp_long"] = tp_long
        df["sl_short"] = sl_short
        df["tp_short"] = tp_short

        return df

    # ------------------------------------------------------------------ #
    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        df = dataframe

        long_cond = (
            df["uptrend"]
            & df["touch_sup"]
            & df["bull_engulf"]
            & df["green"]                                   # candle konfirmasi hijau
            & (df["close"] > df["close"].shift(1))          # close > close candle sebelumnya
            & df["engulf_in_sup"]                           # bukan engulfing menggantung
            & (~df["conf_abnormal"])                        # candle konfirmasi tidak abnormal
            & (df["sl_long"] < df["close"])                 # SL valid di bawah harga
            & (df["atr"] > 0)
            & (df["volume"] > 0)
            & df["vol_confluence"]                          # 🚦/⚡ konfluensi volume
        )

        short_cond = (
            df["downtrend"]
            & df["touch_res"]
            & df["bear_engulf"]
            & df["red"]                                     # candle konfirmasi merah
            & (df["close"] < df["close"].shift(1))
            & df["engulf_in_res"]
            & (~df["conf_abnormal"])
            & (df["sl_short"] > df["close"])                # SL valid di atas harga
            & (df["atr"] > 0)
            & (df["volume"] > 0)
            & df["vol_confluence"]                          # 🚦/⚡ konfluensi volume
        )

        df.loc[long_cond, ["enter_long", "enter_tag"]] = (1, "sr_long")
        df.loc[short_cond, ["enter_short", "enter_tag"]] = (1, "sr_short")
        return df

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # Exit murni lewat custom_stoploss (SL) + custom_exit (TP RR 1:2).
        return dataframe

    # ------------------------------------------------------------------ #
    def _entry_row(self, pair: str, trade: Trade) -> Optional[pd.Series]:
        """Ambil baris candle entry dari analyzed dataframe (deterministik)."""
        df, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        if df is None or df.empty:
            return None
        row = df.loc[df["date"] == trade.open_date_utc]
        if row.empty:
            row = df.loc[df["date"] <= trade.open_date_utc].tail(1)
        if row.empty:
            return None
        return row.iloc[-1]

    def custom_stoploss(self, pair: str, trade: Trade, current_time: datetime,
                        current_rate: float, current_profit: float,
                        **kwargs) -> Optional[float]:
        row = self._entry_row(pair, trade)
        if row is None:
            return None
        if trade.is_short:
            sl = row.get("sl_short")
        else:
            sl = row.get("sl_long")
        if sl is None or not np.isfinite(sl) or sl <= 0:
            return None
        # konversi harga SL absolut -> stoploss relatif thd entry
        return stoploss_from_absolute(
            sl, trade.open_rate, is_short=trade.is_short, leverage=trade.leverage
        )

    def custom_exit(self, pair: str, trade: Trade, current_time: datetime,
                    current_rate: float, current_profit: float, **kwargs):
        row = self._entry_row(pair, trade)
        if row is None:
            return None
        if trade.is_short:
            tp = row.get("tp_short")
            if tp is not None and np.isfinite(tp) and current_rate <= tp:
                return "tp_rr2"
        else:
            tp = row.get("tp_long")
            if tp is not None and np.isfinite(tp) and current_rate >= tp:
                return "tp_rr2"
        return None
