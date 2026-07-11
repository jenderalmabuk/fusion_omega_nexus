# signals/smc_engine.py - v7.7 (OB memory 3 jam)
import pandas as pd
import time
from utils.logger import logger

class SMCEngine:
    def __init__(self, window=5, max_age_candles=12):
        self.window = window
        self.max_age = max_age_candles  # OB hanya valid jika muncul di N candle terakhir
        self.ob_watchlist = {}          # {symbol: timestamp} untuk menandai koin dengan BOS terdeteksi

    def detect_structure(self, df: pd.DataFrame):
        """
        Deteksi Break of Structure (BOS) & ambil swing high/low terakhir.
        Mengembalikan: (structure, last_swing_high, last_swing_low)
        """
        if df is None or len(df) < 30:
            return "NEUTRAL", None, None

        high = df['high'].values
        low = df['low'].values
        close = df['close'].values

        swing_highs = []
        swing_lows = []

        for i in range(self.window, len(df) - self.window):
            # Swing high
            if all(high[i] >= high[i-j] for j in range(1, self.window+1)) and \
               all(high[i] >= high[i+j] for j in range(1, self.window+1)):
                swing_highs.append(high[i])
            # Swing low
            if all(low[i] <= low[i-j] for j in range(1, self.window+1)) and \
               all(low[i] <= low[i+j] for j in range(1, self.window+1)):
                swing_lows.append(low[i])

        if not swing_highs or not swing_lows:
            return "NEUTRAL", None, None

        last_sh = swing_highs[-1]
        last_sl = swing_lows[-1]
        curr_price = close[-1]

        if curr_price > last_sh:
            return "BULLISH_BOS", last_sh, last_sl
        if curr_price < last_sl:
            return "BEARISH_BOS", last_sh, last_sl
        return "RANGING", last_sh, last_sl

    def find_order_block(self, df: pd.DataFrame, direction="BULLISH"):
        """
        Mencari order block segar (dalam max_age terakhir) dengan FVG (imbalance).
        Kembalikan dict dengan harga OB dan high/low candle OB.
        """
        start_idx = max(0, len(df) - self.max_age)

        if direction == "BULLISH":
            for i in range(len(df)-3, start_idx, -1):
                # Candle merah (close < open) sebagai demand
                if df['close'].iloc[i] < df['open'].iloc[i]:
                    # FVG: high candle i < low candle i+2
                    if df['high'].iloc[i] < df['low'].iloc[i+2]:
                        return {'price': df['low'].iloc[i], 'high': df['high'].iloc[i]}
        else:  # BEARISH
            for i in range(len(df)-3, start_idx, -1):
                # Candle hijau (close > open) sebagai supply
                if df['close'].iloc[i] > df['open'].iloc[i]:
                    # FVG: low candle i > high candle i+2
                    if df['low'].iloc[i] > df['high'].iloc[i+2]:
                        return {'price': df['high'].iloc[i], 'low': df['low'].iloc[i]}
        return None

    def get_smc_signal(self, symbol: str, df_15m: pd.DataFrame, df_1m: pd.DataFrame = None):
        """
        Kembalikan (label, ob_price) jika terdeteksi SMC valid.
        Jika df_1m disediakan, lakukan konfirmasi micro-candle.
        label: 
            - "SMC_BULLISH_OTE_CONFIRMED" / "SMC_BEARISH_OTE_CONFIRMED"
            - "SMC_BULLISH_WAITING_CONFIRM" / "SMC_BEARISH_WAITING_CONFIRM"
            - "NONE"
        ob_price: harga order block (untuk logging)
        """
        structure, s_high, s_low = self.detect_structure(df_15m)

        # Jika ada perubahan struktur (BOS), masukkan ke watchlist
        if structure != "NEUTRAL":
            self.ob_watchlist[symbol] = time.time()
            # Bersihkan watchlist lama (lebih dari 3 jam = 10800 detik)
            current = time.time()
            expired = [s for s, ts in self.ob_watchlist.items() if current - ts > 10800]  # <-- DIUBAH
            for s in expired:
                del self.ob_watchlist[s]

        if structure == "NEUTRAL" or s_high is None or s_low is None:
            return "NONE", 0

        curr_price = df_15m['close'].iloc[-1]
        diff = s_high - s_low

        if structure == "BULLISH_BOS":
            ob = self.find_order_block(df_15m, "BULLISH")
            if ob:
                ote_low = s_high - (diff * 0.786)
                ote_high = s_high - (diff * 0.618)
                if ote_low <= curr_price <= ote_high:
                    # Micro-confirmation dengan 1m candle
                    if df_1m is not None and not df_1m.empty:
                        last_1m = df_1m.iloc[-1]
                        if last_1m['close'] > last_1m['open']:
                            return "SMC_BULLISH_OTE_CONFIRMED", ob['price']
                        else:
                            return "SMC_BULLISH_WAITING_CONFIRM", ob['price']
                    else:
                        # Fallback: anggap confirmed jika tidak ada data 1m
                        return "SMC_BULLISH_OTE_CONFIRMED", ob['price']

        elif structure == "BEARISH_BOS":
            ob = self.find_order_block(df_15m, "BEARISH")
            if ob:
                ote_low = s_low + (diff * 0.618)
                ote_high = s_low + (diff * 0.786)
                if ote_low <= curr_price <= ote_high:
                    # Micro-confirmation dengan 1m candle
                    if df_1m is not None and not df_1m.empty:
                        last_1m = df_1m.iloc[-1]
                        if last_1m['close'] < last_1m['open']:
                            return "SMC_BEARISH_OTE_CONFIRMED", ob['price']
                        else:
                            return "SMC_BEARISH_WAITING_CONFIRM", ob['price']
                    else:
                        return "SMC_BEARISH_OTE_CONFIRMED", ob['price']

        return "NONE", 0

    def get_ob_watchlist(self):
        """Mengembalikan daftar simbol yang pernah memiliki BOS dalam 3 jam terakhir."""
        current = time.time()
        return [sym for sym, ts in self.ob_watchlist.items() if current - ts <= 10800]  # <-- DIUBAH

# Inisialisasi global
smc_engine = SMCEngine()