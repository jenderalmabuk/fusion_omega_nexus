# signals/ai_signal_ranker.py - v7.9.7 (VWAP copy, score components, no side effect)
"""
Fusion X Omega Elite v5 - AI Signal Ranker v7.9.7
- Anchored Daily VWAP filter (copy dataframe, no side effect)
- SMC dengan konfirmasi micro-candle (1m)
- Accumulation memory 8 jam
- Breakout diperkuat volume/ADX
- Score components untuk debugging & tuning
"""

from config import (
    WHALE_CONFIDENCE_BOOST, ONCHAIN_BOOST, SMC_BOOST_VALUE, ENABLE_SMC,
    MIN_SIGNAL_SCORE_RANGING, MIN_SIGNAL_SCORE_TRENDING, MIN_SIGNAL_SCORE_HIGH_VOL
)
from whales.redis_db import get_pending_whales
from ml.booster import booster
from utils.logger import logger
import pandas as pd
import time
from datetime import datetime, UTC
from signals.smc_engine import smc_engine

# =========================================================
# SESSION DETECTION (berdasarkan UTC)
# =========================================================
def get_current_session() -> str:
    hour_utc = datetime.now(UTC).hour
    if 1 <= hour_utc < 8:
        return "ASIA"
    elif 8 <= hour_utc < 16:
        return "LONDON"
    else:
        return "NEWYORK"

# =========================================================
# CACHE UNTUK KOIN YANG TERDETEKSI AKUMULASI (8 Jam / 28800s)
# =========================================================
_acc_cache = {}  # {symbol: timestamp}

def _is_accumulated_recently(symbol: str, max_age_sec: int = 28800) -> bool:
    ts = _acc_cache.get(symbol)
    if ts is None:
        return False
    return (time.time() - ts) < max_age_sec

def _mark_accumulated(symbol: str):
    _acc_cache[symbol] = time.time()

# =========================================================
# FUNGSI VIP FAST-TRACK
# =========================================================
def get_accumulated_coins() -> list:
    current_time = time.time()
    acc_list = [sym for sym, ts in _acc_cache.items() if (current_time - ts) < 28800]
    smc_list = smc_engine.get_ob_watchlist()
    full_vip_list = list(set(acc_list + smc_list))
    return full_vip_list

# =========================================================
# 1. WHALE SCORE
# =========================================================
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

# =========================================================
# 2. SILENT ACCUMULATION DETECTOR
# =========================================================
def detect_silent_accumulation(adv: dict, mtf_result: dict) -> tuple[bool, float]:
    vol_ratio = adv.get("vol_ratio", 1.0)
    price_change_24h = adv.get("price_change_24h_pct", 0.0)
    cvd = adv.get("cvd", 0.0)
    oi_change_15m = adv.get("oi_change_15m_pct", 0.0)

    is_accumulating = (
        vol_ratio < 0.85 and
        price_change_24h < 1.0 and
        cvd > 0 and
        oi_change_15m > 0.5 and
        mtf_result.get("trend_15m") in ["DOWN", "NEUTRAL"] and
        mtf_result.get("trend_1m") == "UP"
    )
    return is_accumulating, 35.0 if is_accumulating else 0.0

# =========================================================
# 3. ORDER BLOCK FILTER
# =========================================================
def ob_breakout_filter(price: float, poc_price: float, regime: dict) -> tuple[bool, str]:
    if poc_price == 0:
        return True, "OK"
    distance_to_poc = abs(price - poc_price) / poc_price * 100
    if distance_to_poc < 1.0 and abs(regime.get("atr_pct", 0)) > 1.5:
        return False, "OB Wick >1.5% - skip"
    return True, "OK"

# =========================================================
# 4. SQUEEZE ENGINE (ADAPTIVE)
# =========================================================
def compute_squeeze(funding_rate: float, price_change_15m: float, oi_change_15m: float):
    squeeze_score = min(50, abs(funding_rate) * 400 + abs(price_change_15m) * 10 + abs(oi_change_15m) * 8)
    squeeze_long = 0
    squeeze_short = 0
    weak_factor = min(0.7, abs(price_change_15m) / 1.5)

    if funding_rate < 0:
        if price_change_15m > 0:
            squeeze_short = squeeze_score
        else:
            squeeze_short = squeeze_score * weak_factor
    elif funding_rate > 0:
        if price_change_15m < 0:
            squeeze_long = squeeze_score
        else:
            squeeze_long = squeeze_score * weak_factor

    phase = "NONE"
    if oi_change_15m > 3.0 and abs(price_change_15m) > 1.2:
        phase = "EXPLOSION"
    elif oi_change_15m > 1.5:
        phase = "BUILDUP"
    return squeeze_long, squeeze_short, phase, squeeze_score

# =========================================================
# 5. ABSORPTION DETECTOR
# =========================================================
def detect_absorption(vol_ratio: float, price_change_15m: float) -> tuple[bool, float]:
    if vol_ratio > 2.5 and abs(price_change_15m) < 0.3:
        return True, min(12, vol_ratio * 2)
    return False, 0

# =========================================================
# 6. QUALITY BREAKOUT ENGINE (Volume & ADX)
# =========================================================
def is_valid_breakout(df_15m: pd.DataFrame, direction: str, vol_ratio: float = 1.0, adx_15m: float = 25.0) -> tuple[bool, str]:
    if df_15m is None or df_15m.empty or len(df_15m) < 3:
        return True, "NO_DATA_BYPASS"

    if vol_ratio < 1.3:
        return False, f"LOW_VOL_BREAKOUT({vol_ratio:.1f})"
    if adx_15m < 22.0:
        return False, f"WEAK_ADX_BREAKOUT({adx_15m:.1f})"

    c_close = df_15m['close'].iloc[-1]
    c_open = df_15m['open'].iloc[-1]
    c_high = df_15m['high'].iloc[-1]
    c_low = df_15m['low'].iloc[-1]
    p_high = df_15m['high'].iloc[-2]
    p_low = df_15m['low'].iloc[-2]

    current_range = c_high - c_low
    prev_range = p_high - p_low

    if current_range < (prev_range * 1.1):
        return False, "WEAK_MOMENTUM_BREAKOUT"

    if direction == "LONG":
        if c_close <= p_high:
            return False, "NO_BREAKOUT"
        body_top = max(c_open, c_close)
        top_wick = c_high - body_top
        if top_wick > (current_range * 0.40):
            return False, "TOP_WICK_REJECTION(FAKEOUT)"
        return True, "VALID_BULL_BREAKOUT"

    elif direction == "SHORT":
        if c_close >= p_low:
            return False, "NO_BREAKOUT"
        body_bottom = min(c_open, c_close)
        bottom_wick = body_bottom - c_low
        if bottom_wick > (current_range * 0.40):
            return False, "BOTTOM_WICK_REJECTION(FAKEOUT)"
        return True, "VALID_BEAR_BREAKOUT"
    return False, "UNKNOWN"

# =========================================================
# 7. VWAP CALCULATION (ANCHORED DAILY VWAP) — NO SIDE EFFECT
# =========================================================
def calculate_vwap(df: pd.DataFrame) -> float:
    """Copy dataframe to avoid modifying original data."""
    if df is None or df.empty or len(df) < 5:
        return None

    # IMPORTANT: work on a copy to avoid side effects
    df = df.copy()

    if not isinstance(df.index, pd.DatetimeIndex):
        try:
            df.index = pd.to_datetime(df.index)
        except Exception:
            if 'open_time' in df.columns:
                df['open_time'] = pd.to_datetime(df['open_time'])
                df.set_index('open_time', inplace=True)
            else:
                return None

    df['date'] = df.index.date
    typical_price = (df['high'] + df['low'] + df['close']) / 3
    volume = df['volume']

    df['cum_vol'] = volume.groupby(df['date']).cumsum()
    df['cum_tpv'] = (typical_price * volume).groupby(df['date']).cumsum()

    vwap_series = df['cum_tpv'] / df['cum_vol'].replace(0, 1)
    last_vwap = vwap_series.iloc[-1]

    return float(last_vwap) if not pd.isna(last_vwap) else None

# =========================================================
# 8. MAIN RANK ENGINE v7.9.7
# =========================================================
def rank_signal(
    symbol: str, mtf_result: dict, regime: dict, oi_change_1h: float,
    oi_change_15m: float, funding_rate: float, cvd: float, cvd_zscore: float,
    price: float, poc_price: float, vol_ratio: float, rsi: float, whale_score: int,
    imbalance: float = 0.0, price_change_15m_pct: float = 0.0,
    atr_pct: float = 0.0, atr_pct_prev: float = None,
    df_15m: pd.DataFrame = None, df_1m: pd.DataFrame = None,
) -> dict:

    adx_15m = mtf_result.get("adx_15m", 25.0)
    bbw_15m = mtf_result.get("bbw_15m", 0.20)

    # HARD BLOCK
    if adx_15m < 18.0 and bbw_15m < 0.12:
        return {
            "symbol": symbol, "direction": "NONE", "probability": 0.0,
            "boost_reason": f"DEAD_ZONE_VETO(ADX:{adx_15m:.1f}/BBW:{bbw_15m:.2f})",
            "quadrant": "NONE", "score": 0.0, "min_score_required": 999, "is_breakout": False,
            "acc_boost_applied": False, "acc_boost_info": {},
            "score_components": {}
        }

    weak_trend_penalty = 0.85 if 18.0 <= adx_15m < 22.0 else 1.0
    boost_reason = f"WEAK_TREND({adx_15m:.1f}) " if weak_trend_penalty != 1.0 else ""

    buy_score = 0.0
    sell_score = 0.0
    quadrant = "NONE"

    # A. SILENT ACCUMULATION
    adv_bundle = {
        "vol_ratio": vol_ratio,
        "price_change_24h_pct": (price - poc_price) / poc_price * 100 if poc_price > 0 else 0,
        "cvd": cvd,
        "oi_change_15m_pct": oi_change_15m
    }
    acc, acc_bonus = detect_silent_accumulation(adv_bundle, mtf_result)
    if acc:
        buy_score += acc_bonus
        boost_reason += "SILENT_ACCUMULATION "
        _mark_accumulated(symbol)

    # B. QUADRANT CORE
    up_count = sum(1 for k in ["trend_1m", "trend_5m", "trend_15m"] if mtf_result.get(k) == "UP")
    price_trend = "UP" if up_count >= 2 else "DOWN"
    cvd_trend = "UP" if cvd_zscore > 0.4 else "DOWN"

    quadrant_score = 0
    if oi_change_1h > 0.065 and price_trend == "UP" and cvd_trend == "UP" and abs(funding_rate) < 0.05:
        buy_score += 48
        quadrant = "1A_STRONG_BULL"
        quadrant_score = 48
        boost_reason += "1A_STRONG_BULL "
    elif oi_change_1h < -0.065 and price_trend == "DOWN" and cvd_trend == "DOWN" and abs(funding_rate) < 0.05:
        sell_score += 48
        quadrant = "2A_STRONG_BEAR"
        quadrant_score = 48
        boost_reason += "2A_STRONG_BEAR "
    elif oi_change_1h > 0.065 and price_trend == "UP":
        buy_score += 18
        quadrant = "1B_BULL_WEAK"
        quadrant_score = 18
    elif oi_change_1h < -0.065 and price_trend == "DOWN":
        sell_score += 18
        quadrant = "2B_BEAR_WEAK"
        quadrant_score = 18

    # C. REGIME
    regime_label = regime.get("label", "RANGING")
    if regime_label == "TRENDING":
        buy_score += 14 if buy_score > 0 else -14
        boost_reason += "TRENDING "
    if vol_ratio > 1.5:
        buy_score += 9 if buy_score > 0 else -9
        boost_reason += "HIGH_VOL "

    # D. OB FILTER
    ob_ok, ob_reason = ob_breakout_filter(price, poc_price, regime)
    if not ob_ok:
        return {
            "symbol": symbol, "direction": "NONE", "probability": 0.0,
            "boost_reason": f"OB_BLOCKED({ob_reason})",
            "quadrant": quadrant, "score": 0.0, "min_score_required": 999, "is_breakout": False,
            "acc_boost_applied": False, "acc_boost_info": {},
            "score_components": {}
        }

    # E. WHALE DIRECTIONAL BOOST
    whale_dir = "LONG" if cvd > 0 else "SHORT"
    whale_boost_value = 0
    if whale_score > 48:
        if whale_dir == "LONG":
            buy_score += WHALE_CONFIDENCE_BOOST
            whale_boost_value = WHALE_CONFIDENCE_BOOST
            boost_reason += f"CEX_Whale_LONG({whale_score}) "
        else:
            sell_score += WHALE_CONFIDENCE_BOOST
            whale_boost_value = WHALE_CONFIDENCE_BOOST
            boost_reason += f"CEX_Whale_SHORT({whale_score}) "

    onchain_boost_value = 0
    for whale in get_pending_whales():
        if whale["symbol"] == symbol:
            if whale_dir == "LONG":
                buy_score += ONCHAIN_BOOST
                onchain_boost_value = ONCHAIN_BOOST
            else:
                sell_score += ONCHAIN_BOOST
                onchain_boost_value = ONCHAIN_BOOST
            boost_reason += f"OnChain_{whale_dir}({whale['percent']:.1f}%) "
            break

    # F. SQUEEZE ENGINE
    sq_long, sq_short, sq_phase, sq_grad = compute_squeeze(
        funding_rate, price_change_15m_pct, oi_change_15m
    )
    if sq_phase == "BUILDUP":
        buy_score -= (sq_long * 0.5)
        sell_score -= (sq_short * 0.5)
        boost_reason += "SQ_BUILDUP "
    elif sq_phase == "EXPLOSION":
        if adx_15m >= 25.0:
            if cvd > 0 and price_trend == "UP":
                buy_score += 18
                boost_reason += "🚀 BREAKOUT_L(+18) "
            elif cvd < 0 and price_trend == "DOWN":
                sell_score += 18
                boost_reason += "🚀 BREAKOUT_S(+18) "
        else:
            buy_score -= 10
            sell_score -= 10
            boost_reason += "FAKE_EXPLOSION_PENALTY "

    # G. ABSORPTION
    is_abs, abs_bonus = detect_absorption(vol_ratio, price_change_15m_pct)
    if is_abs:
        if cvd > 0:
            buy_score += abs_bonus
            boost_reason += f"ABSORB_BULL({abs_bonus:.0f}) "
        else:
            sell_score += abs_bonus
            boost_reason += f"ABSORB_BEAR({abs_bonus:.0f}) "

    # H. ANCHORED VWAP PENALTY
    vwap_penalty = 0
    vwap = None
    if df_15m is not None and not df_15m.empty:
        vwap = calculate_vwap(df_15m)   # menggunakan copy di dalam fungsi
    if vwap is not None and vwap > 0:
        if cvd > 0 and price < vwap * 0.998:
            buy_score -= 15
            vwap_penalty = -15
            boost_reason += "BELOW_VWAP_PENALTY "
        elif cvd < 0 and price > vwap * 1.002:
            sell_score -= 15
            vwap_penalty = -15
            boost_reason += "ABOVE_VWAP_PENALTY "

    # I. ML BOOST
    ml_boost_value = 0
    try:
        from config import DISABLE_ML_BOOST
        if DISABLE_ML_BOOST:
            ml_boost = 0.0
        else:
            ml_boost = booster.get_boost({
                "regime": regime_label, "cvd": cvd, "oi_change_1h": oi_change_1h,
                "vol_ratio": vol_ratio, "rsi": rsi, "imbalance": imbalance,
                "whale_score": whale_score, "early_session": False,
                "squeeze_score": sq_grad, "squeeze_phase": sq_phase
            })
    except ImportError:
        ml_boost = 0.0

    if buy_score >= sell_score:
        buy_score += ml_boost
        ml_boost_value = ml_boost
    else:
        sell_score += ml_boost
        ml_boost_value = ml_boost
    if ml_boost != 0:
        boost_reason += f"ML{'+' if ml_boost > 0 else ''}{ml_boost:.1f} "

    # J. ENHANCED SMC ENGINE
    smc_boost_applied = False
    smc_label = "NONE"
    smc_boost_value = 0
    if ENABLE_SMC and df_15m is not None:
        smc_label, ob_price = smc_engine.get_smc_signal(symbol, df_15m, df_1m)
        if smc_label != "NONE":
            if "CONFIRMED" in smc_label:
                smc_boost = SMC_BOOST_VALUE
                smc_boost_value = smc_boost
                if "BULLISH" in smc_label:
                    buy_score += smc_boost
                else:
                    sell_score += smc_boost
                smc_boost_applied = True
                boost_reason += f"{smc_label}(+{smc_boost}) "
            else:
                boost_reason += f"{smc_label}(no_boost) "

    # K. FINAL DIFF
    diff = buy_score - sell_score
    if weak_trend_penalty != 1.0:
        diff *= weak_trend_penalty

    # L. DYNAMIC MIN SCORE
    base_min = {
        "RANGING": MIN_SIGNAL_SCORE_RANGING,
        "TRENDING": MIN_SIGNAL_SCORE_TRENDING,
        "HIGH_VOL": MIN_SIGNAL_SCORE_HIGH_VOL
    }.get(regime_label, 42)
    atr_adjustment = min(10, int(atr_pct * 2.5))
    vol_discount = min(8, int(vol_ratio * 2.0))
    min_score = max(35, base_min - atr_adjustment - vol_discount)

    if (atr_adjustment + vol_discount) > 5:
        boost_reason += f"Dyn_Score_Drop(-{int(atr_adjustment + vol_discount)}) "
    if regime_label == "RANGING" and diff < 0:
        min_score += 5

    # M. ANTI-FOMO
    if diff >= min_score:
        if rsi > 78.0 or price_change_15m_pct > 2.5:
            diff -= 20
            boost_reason += "⚠️ ANTI-FOMO_L_PENALTY "
    elif diff <= -min_score:
        if rsi < 22.0 or price_change_15m_pct < -2.5:
            diff += 20
            boost_reason += "⚠️ ANTI-FOMO_S_PENALTY "

    # N. TENTUKAN ARAH AWAL
    direction = "LONG" if diff >= min_score else "SHORT" if diff <= -min_score else "NONE"

    # O. ENTRY TRIGGER (Breakout)
    is_breakout_candle_valid = False
    if direction != "NONE" and df_15m is not None:
        valid_breakout, bo_reason = is_valid_breakout(df_15m, direction, vol_ratio, adx_15m)
        if not valid_breakout:
            if smc_label not in ("NONE", "WAITING"):
                boost_reason += "SMC_PULLBACK_CONFIRMED "
            else:
                direction = "NONE"
                boost_reason += f"REJECTED: {bo_reason} "
        else:
            boost_reason += f"CONFIRMED: {bo_reason} "
            is_breakout_candle_valid = True

    # P. SESSION-AWARE ACCUMULATION BOOST
    acc_boost_applied = False
    original_diff = diff
    if direction != "NONE" and is_breakout_candle_valid and _is_accumulated_recently(symbol):
        diff += 15
        acc_boost_applied = True
        boost_reason += f"ACC_BOOST({get_current_session()}) "
        if diff >= min_score:
            direction = "LONG"
        elif diff <= -min_score:
            direction = "SHORT"
        else:
            direction = "NONE"

    # Q. QUADRANT VETO
    if direction != "NONE":
        if quadrant in ("NONE", "UNKNOWN") and not is_breakout_candle_valid and smc_label == "NONE":
            direction = "NONE"
            boost_reason += "QUADRANT_VETO(NO_DIRECTION) "

    # R. PROBABILITY ADJUSTMENT
    probability = min(0.95, 0.5 + abs(diff) / 80)
    if sq_phase == "EXPLOSION" and adx_15m >= 25.0:
        probability = min(0.98, probability * 1.10)

    is_breakout = is_breakout_candle_valid

    # ========== SCORE COMPONENTS ==========
    score_components = {
        "quadrant": quadrant_score,
        "whale": whale_boost_value + onchain_boost_value,
        "vwap": vwap_penalty,
        "smc": smc_boost_value,
        "ml": ml_boost_value,
    }

    logger.info(
        f"[{symbol}] {quadrant} | Score {diff:+.1f} | Prob {probability:.1%} | "
        f"Squeeze: {sq_phase} | RSI:{rsi:.1f} | ADX:{adx_15m:.1f} BBW:{bbw_15m:.3f} | "
        f"Boost: {boost_reason.strip() or 'None'} | Direction: {direction} | "
        f"Regime: {regime_label} (min {min_score}) | Components: {score_components}"
    )

    return {
        "symbol": symbol, "direction": direction, "probability": round(probability * 100, 1),
        "boost_reason": boost_reason.strip() or "None", "quadrant": quadrant,
        "score": diff, "min_score_required": min_score, "is_breakout": is_breakout,
        "acc_boost_applied": acc_boost_applied,
        "acc_boost_info": {
            "symbol": symbol,
            "direction": direction,
            "score_before": original_diff,
            "min_score_before": min_score,
            "session": get_current_session()
        } if acc_boost_applied else {},
        "smc_applied": smc_boost_applied,
        "smc_label": smc_label,
        "score_components": score_components,
    }