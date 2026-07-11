# signals/squeeze_detector.py
"""
Liquidation Squeeze Detector
Mendeteksi potensi long/short squeeze berdasarkan data derivatif (OI, funding, harga, volume).
"""

from __future__ import annotations

import pandas as pd
from typing import Any, Dict


def detect_liquidation_squeeze(
    symbol: str,
    adv: Dict[str, Any],
    df_15m: pd.DataFrame | None = None,
) -> Dict[str, Any]:
    """
    Mendeteksi apakah sedang terjadi liquidation squeeze (long atau short).

    Args:
        symbol: Simbol trading (untuk logging, tidak digunakan dalam perhitungan)
        adv: Data advanced metrics (harus mengandung oi_change_1h_pct, funding_rate_pct)
        df_15m: DataFrame klines 15 menit (minimal 4 candle terakhir)

    Returns:
        Dict dengan key:
            - type: "LONG_SQUEEZE", "SHORT_SQUEEZE", atau "NONE"
            - score: 0-100 (tingkat keyakinan)
            - oi_surge: bool (apakah OI naik >2% dalam 1 jam)
            - funding_extreme: bool (funding rate >0.05% atau <-0.05%)
            - price_change_1h_pct: float (perubahan harga 1 jam dalam persen)
            - volume_spike: bool (volume candle terakhir >2x rata-rata 20 candle)
    """
    # Default response
    default = {
        "type": "NONE",
        "score": 0.0,
        "oi_surge": False,
        "funding_extreme": False,
        "price_change_1h_pct": 0.0,
        "volume_spike": False,
    }

    # --- 1. Validasi Data ---
    oi_change_1h = float(adv.get("oi_change_1h_pct", 0.0) or 0.0)
    funding_rate = float(adv.get("funding_rate_pct", 0.0) or 0.0)  # Sudah dalam persen (misal 0.01 untuk 0.01%)

    # Threshold
    OI_SURGE_THRESHOLD = 2.0          # OI naik >2% dalam 1 jam
    FUNDING_EXTREME_THRESHOLD = 0.05  # >0.05% atau <-0.05%
    PRICE_CHANGE_THRESHOLD = 3.0      # Harga berubah >3% dalam 1 jam
    VOLUME_SPIKE_MULTIPLIER = 2.0     # Volume candle terakhir >2x rata-rata 20 candle

    oi_surge = oi_change_1h > OI_SURGE_THRESHOLD
    is_funding_extreme_long = funding_rate > FUNDING_EXTREME_THRESHOLD
    is_funding_extreme_short = funding_rate < -FUNDING_EXTREME_THRESHOLD
    funding_extreme = is_funding_extreme_long or is_funding_extreme_short

    # Jika data tidak memenuhi syarat minimal, langsung return NONE
    if not oi_surge and not funding_extreme:
        return default

    # --- 2. Pemicu Harga (Price Action) ---
    price_change_1h_pct = 0.0
    if df_15m is not None and not df_15m.empty and len(df_15m) >= 4:
        try:
            price_start = float(df_15m["close"].iloc[-4])   # 1 jam yang lalu (4 candle 15m)
            price_now = float(df_15m["close"].iloc[-1])
            if price_start > 0:
                price_change_1h_pct = ((price_now - price_start) / price_start) * 100.0
        except Exception:
            pass

    # --- 3. Konfirmasi Volume ---
    volume_spike = False
    if df_15m is not None and not df_15m.empty and len(df_15m) >= 20:
        try:
            vol_now = float(df_15m["volume"].iloc[-1])
            vol_avg = float(df_15m["volume"].tail(20).mean())
            if vol_avg > 0:
                volume_spike = vol_now > (vol_avg * VOLUME_SPIKE_MULTIPLIER)
        except Exception:
            pass

    # --- 4. Logika Deteksi Squeeze ---
    squeeze_type = "NONE"
    squeeze_score = 0.0

    # Long squeeze: pasar bullish ekstrem (funding positif tinggi) tapi harga turun tajam
    if is_funding_extreme_long and oi_surge and price_change_1h_pct < -PRICE_CHANGE_THRESHOLD:
        squeeze_type = "LONG_SQUEEZE"
        squeeze_score = 70.0
        if volume_spike:
            squeeze_score = 90.0

    # Short squeeze: pasar bearish ekstrem (funding negatif tinggi) tapi harga naik tajam
    elif is_funding_extreme_short and oi_surge and price_change_1h_pct > PRICE_CHANGE_THRESHOLD:
        squeeze_type = "SHORT_SQUEEZE"
        squeeze_score = 70.0
        if volume_spike:
            squeeze_score = 90.0

    return {
        "type": squeeze_type,
        "score": squeeze_score,
        "oi_surge": oi_surge,
        "funding_extreme": funding_extreme,
        "price_change_1h_pct": round(price_change_1h_pct, 2),
        "volume_spike": volume_spike,
    }