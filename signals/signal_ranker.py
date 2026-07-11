# signals/signal_ranker.py - v14 (FIXED: added pandas import)
import time
import pandas as pd   # <-- DITAMBAHKAN
from collections import deque
from typing import Dict, Any
from utils.logger import logger
from config import (
    MIN_SIGNAL_SCORE, MIN_CONFIDENCE,
    MIN_SIGNAL_SCORE_RANGING, MIN_SIGNAL_SCORE_TRENDING, MIN_SIGNAL_SCORE_HIGH_VOL,
    WHALE_CONFIDENCE_BOOST, ONCHAIN_BOOST,
    ENABLE_SMC, SMC_BOOST_VALUE
)
from whales.redis_db import get_pending_whales
from ml.booster import booster
from signals.smc_engine import smc_engine
from signals.silent_accumulation import (
    detect_silent_accumulation_v2,
    get_accumulated_coins as get_recent_accumulation_coins,
    get_silent_accumulation_details,
    mark_silent_accumulation as _mark_accumulated_record,
)

# OI History Cache per symbol
_oi_history: Dict[str, deque] = {}
_OI_HISTORY_MAXLEN = 60

def update_oi_history(symbol: str, oi_value: float) -> Dict[str, float]:
    """Update OI history dan hitung perubahan persentase"""
    if symbol not in _oi_history:
        _oi_history[symbol] = deque(maxlen=_OI_HISTORY_MAXLEN)
    _oi_history[symbol].append(oi_value)
    
    result = {"oi_change_15m": 0.0, "oi_change_1h": 0.0, "oi_samples": len(_oi_history[symbol])}
    
    dq = _oi_history[symbol]
    if len(dq) >= 2:
        oi_now = dq[-1]
        if len(dq) >= 16:
            oi_15m_ago = dq[-16]
            if oi_15m_ago > 0:
                result["oi_change_15m"] = (oi_now - oi_15m_ago) / oi_15m_ago * 100
        if len(dq) >= 60:
            oi_1h_ago = dq[-60]
            if oi_1h_ago > 0:
                result["oi_change_1h"] = (oi_now - oi_1h_ago) / oi_1h_ago * 100
    return result

def get_oi_changes(symbol: str, current_oi: float) -> Dict[str, float]:
    return update_oi_history(symbol, current_oi)

# Accumulation compatibility wrappers

def _mark_accumulated(symbol: str):
    details = get_silent_accumulation_details(symbol)
    if details.get('is_accumulating'):
        _mark_accumulated_record(symbol, details)


def get_accumulated_coins() -> list:
    smc_list = smc_engine.get_ob_watchlist() if hasattr(smc_engine, 'get_ob_watchlist') else []
    return get_recent_accumulation_coins(include_smc=True, smc_watchlist=smc_list)


def get_silent_accumulation_watchlist() -> list:
    return get_recent_accumulation_coins(include_smc=False)


def compute_whale_score(adv: dict) -> int:
    try:
        score = 0.0
        oi15 = adv.get("oi_change_15m_pct", 0.0)
        oi1h = adv.get("oi_change_1h_pct", 0.0)
        if oi15 > 3.0: score += 25
        elif oi15 > 1.5: score += 15
        elif oi1h > 4.0: score += 10

        cvd_z = abs(adv.get("cvd_zscore", 0.0))
        if cvd_z > 3.0: score += 25
        elif cvd_z > 2.0: score += 15
        elif cvd_z > 1.0: score += 5

        vol = adv.get("vol_ratio", 1.0)
        if vol > 4.0: score += 25
        elif vol > 2.5: score += 15
        elif vol > 1.5: score += 5

        imb = abs(adv.get("imbalance", 0.0))
        if imb > 0.6: score += 25
        elif imb > 0.4: score += 15
        elif imb > 0.2: score += 5
        return max(15, min(100, int(score)))
    except Exception:
        return 50

def calculate_vwap(df):
    """
    Menghitung VWAP dengan zero check untuk menghindari division by zero.
    """
    if df is None or df.empty or len(df) < 5:
        return 0.0
    try:
        typical = (df['high'] + df['low'] + df['close']) / 3
        volume = df['volume']
        
        total_volume = volume.sum()
        # ========== ZERO CHECK ==========
        if total_volume == 0 or pd.isna(total_volume):
            return 0.0
            
        total_tpv = (typical * volume).sum()
        if total_tpv == 0 or pd.isna(total_tpv):
            return 0.0
            
        vwap = total_tpv / total_volume
        return float(vwap) if not pd.isna(vwap) else 0.0
    except Exception as e:
        logger.debug(f"VWAP calculation error: {e}")
        return 0.0

def rank_signal(
    symbol: str,
    adv: Dict,
    mtf_result: Dict,
    df_15m=None,
    df_1m=None,
    **kwargs
) -> Dict[str, Any]:
    
    # ========== EARLY EXIT: jika data invalid, langsung return NONE ==========
    if not adv.get("data_valid", True):
        logger.debug(f"[SIGNAL] {symbol} SKIP: data_valid=False")
        return {
            "symbol": symbol,
            "score": 0,
            "direction": "NONE",
            "confidence": 0,
            "components": {},
            "quadrant": "NEUTRAL",
            "priority_score": 0,
            "regime": "RANGING",
            "smc_label": "NONE",
        }
    
    # Ambil data dari adv
    price = adv.get("price", 0)
    oi_1h = adv.get("oi_change_1h_pct", 0)
    oi_15m = adv.get("oi_change_15m_pct", 0)
    cvd = adv.get("cvd", 0)
    cvd_z = adv.get("cvd_zscore", 0)
    funding = adv.get("funding_rate_pct", 0)
    vol_ratio = adv.get("vol_ratio", 1.0)
    rsi = adv.get("rsi", 50)
    atr_pct = adv.get("atr_pct", 2.5)
    poc_price = adv.get("poc_price", price)
    quote_volume = adv.get("quoteVolume", 0)

    silent_acc = detect_silent_accumulation_v2(df_15m, adv=adv, mtf_result=mtf_result, lookback=20, baseline_bars=50)
    silent_acc_bonus = 0.0
    if silent_acc.get("accumulation_state") == "ACCUMULATION_READY":
        silent_acc_bonus = 8.0
    elif silent_acc.get("accumulation_state") == "VALID_ACCUMULATION":
        silent_acc_bonus = 4.0
    elif silent_acc.get("accumulation_state") == "EARLY_ACCUMULATION":
        silent_acc_bonus = 1.5
    if silent_acc.get("is_accumulating"):
        _mark_accumulated_record(symbol, silent_acc)

    # Update OI history jika ada OI value
    oi_value = adv.get("oi", 0)
    if oi_value > 0:
        oi_changes = update_oi_history(symbol, oi_value)
        if oi_1h == 0 and oi_changes.get("oi_change_1h", 0) != 0:
            oi_1h = oi_changes["oi_change_1h"]
        if oi_15m == 0 and oi_changes.get("oi_change_15m", 0) != 0:
            oi_15m = oi_changes["oi_change_15m"]

    # Komponen
    quadrant_score = 0
    whale_component = 0
    vwap_component = 0
    smc_component = 0
    ml_component = 0
    direction_hint = None

    # 1. Quadrant - use regime info passed from caller or compute locally
    up_count = sum(1 for k in ["trend_1m", "trend_5m", "trend_15m"] if mtf_result.get(k) == "UP")
    trend = "UP" if up_count >= 2 else "DOWN" if up_count <= 1 else "NEUTRAL"
    regime_info = kwargs.get("regime_info", {})
    regime_label = regime_info.get("label", adv.get("regime_label", "RANGING"))
    quadrant_name = regime_info.get("quadrant", "UNKNOWN")
    adx_value = regime_info.get("adx", 20.0)
    structure = regime_info.get("structure", "UNCLEAR")

    if regime_label == "TRENDING" and structure == "UPTREND" and cvd_z > 0.3:
        quadrant_score = 40
        direction_hint = "LONG"
    elif regime_label == "TRENDING" and structure == "DOWNTREND" and cvd_z < -0.3:
        quadrant_score = -40
        direction_hint = "SHORT"
    elif regime_label == "TRENDING" and structure == "UPTREND":
        quadrant_score = 22
        direction_hint = "LONG"
    elif regime_label == "TRENDING" and structure == "DOWNTREND":
        quadrant_score = -22
        direction_hint = "SHORT"
    elif oi_1h > 0.05 and trend == "UP" and cvd_z > 0.3:
        quadrant_score = 18
        direction_hint = "LONG"
    elif oi_1h < -0.05 and trend == "DOWN" and cvd_z < -0.3:
        quadrant_score = -18
        direction_hint = "SHORT"
    elif trend == "UP" and cvd > 0:
        quadrant_score = 8
        direction_hint = "LONG"
    elif trend == "DOWN" and cvd < 0:
        quadrant_score = -8
        direction_hint = "SHORT"

    # 2. Whale
    whale_raw = compute_whale_score(adv)
    if whale_raw > 70:
        whale_component = 20 if cvd > 0 else -20
    elif whale_raw > 50:
        whale_component = 10 if cvd > 0 else -10
    elif whale_raw > 30:
        whale_component = 5 if cvd > 0 else -5

    # 3. VWAP (dengan zero check di dalam fungsi)
    if df_15m is not None and not df_15m.empty:
        vwap = calculate_vwap(df_15m)
        if vwap != 0.0 and price > 0:
            if cvd > 0 and price < vwap * 0.99:
                vwap_component = -10
            elif cvd < 0 and price > vwap * 1.01:
                vwap_component = -10
            elif cvd > 0 and price > vwap * 1.01:
                vwap_component = 10
            elif cvd < 0 and price < vwap * 0.99:
                vwap_component = 10

    # 4. SMC
    smc_label = "NONE"
    if ENABLE_SMC and df_15m is not None and not df_15m.empty:
        try:
            smc_label, ob_price = smc_engine.get_smc_signal(symbol, df_15m, df_1m)
            if smc_label != "NONE" and "CONFIRMED" in smc_label:
                smc_component = SMC_BOOST_VALUE if "BULLISH" in smc_label else -SMC_BOOST_VALUE
        except Exception as e:
            logger.debug(f"SMC error {symbol}: {e}")

    # 5. Silent accumulation precursor (bullish only, not a trigger by itself)
    accumulation_component = 0.0
    if silent_acc.get("is_accumulating"):
        if direction_hint == "SHORT":
            accumulation_component = 0.0
        elif quadrant_score >= 0:
            accumulation_component = silent_acc_bonus
        else:
            accumulation_component = min(2.0, silent_acc_bonus * 0.25)

    # 6. ML
    ml_raw = 0.0
    try:
        ml_raw = booster.get_boost({
            "regime": adv.get("regime_label", "RANGING"),
            "cvd": cvd,
            "oi_change_1h": oi_1h,
            "vol_ratio": vol_ratio,
            "rsi": rsi,
            "imbalance": adv.get("imbalance", 0),
            "whale_score": whale_raw,
            "early_session": False
        })
        ml_component = max(-20.0, min(20.0, ml_raw))
    except:
        ml_component = 0

    # ========== TOTAL SCORE (TANPA NORMALISASI) ==========
    total_score = quadrant_score + whale_component + vwap_component + smc_component + accumulation_component + ml_component
    total_score = max(-100, min(100, total_score))

    # ========== FALLBACK SIGNAL ==========
    if quadrant_score == 0 and whale_component == 0 and vwap_component == 0 and smc_component == 0:
        fallback_score = ml_component
        if rsi > 60 and cvd > 0:
            fallback_score += 10
        elif rsi < 40 and cvd < 0:
            fallback_score -= 10
        if abs(fallback_score) > abs(total_score):
            total_score = fallback_score

    # ========== DYNAMIC THRESHOLD ==========
    if regime_label == "TRENDING":
        threshold = MIN_SIGNAL_SCORE_TRENDING
    elif regime_label == "HIGH_VOL":
        threshold = MIN_SIGNAL_SCORE_HIGH_VOL
    else:
        threshold = MIN_SIGNAL_SCORE_RANGING

    # Direction
    if total_score >= threshold:
        direction = "LONG"
    elif total_score <= -threshold:
        direction = "SHORT"
    else:
        direction = "NONE"

    # ========== CONFIDENCE ==========
    confidence = min(1.0, abs(total_score) / 100.0)
    if quote_volume < 1_000_000:
        confidence *= 0.7
    if atr_pct < 1.0:
        confidence *= 0.8
        
    # Priority score
    priority_score = abs(total_score) * confidence
    vip_list = get_accumulated_coins()
    if symbol in vip_list:
        priority_score += 15

    # ========== LOGGING ==========
    oi_sample_count = len(_oi_history.get(symbol, []))
    logger.info(
        f"[SIGNAL] {symbol} | Score: {total_score:+6.1f} | Dir: {direction:5} | "
        f"Conf: {confidence:.2f} | Priority: {priority_score:.1f} | "
        f"Components: Q:{quadrant_score:+3d} W:{whale_component:+3d} V:{vwap_component:+3d} "
        f"S:{smc_component:+3d} A:{accumulation_component:+4.1f} M:{ml_component:+5.1f} | "
        f"OI_1h:{oi_1h:+6.2f}% CVD:{cvd:+10.0f} Vol:{vol_ratio:.2f} RSI:{rsi:.0f} | "
        f"ATR:{atr_pct:.2f}% VolUSD:${quote_volume/1e6:.1f}M OI_samples:{oi_sample_count}"
    )

    return {
        "symbol": symbol,
        "score": total_score,
        "direction": direction,
        "confidence": confidence,
        "components": {
            "quadrant": quadrant_score,
            "whale": whale_component,
            "vwap": vwap_component,
            "smc": smc_component,
            "silent_accumulation": accumulation_component,
            "ml": ml_component,
        },
        "quadrant": direction_hint or "NEUTRAL",
        "priority_score": priority_score,
        "regime": regime_label,
        "smc_label": smc_label,
    }
