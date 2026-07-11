# signals/hybrid_signal_ranker.py
# Fusion X Omega Elite v14.5
# PHASE 1 SYNCHRONIZED HYBRID SIGNAL RANKER

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import math
import numpy as np
import pandas as pd

RANGING_LOW_CONF_THRESHOLD = 0.27
WEAK_FUEL_MIN_ABS_OI_15M = 0.15
WEAK_FUEL_MIN_ABS_OI_1H = 0.20
WEAK_FUEL_MIN_VOL_RATIO = 0.40
WEAK_FUEL_MIN_CVD_USD = 250_000.0
DEAD_ZONE_ADX_MAX = 14.0
DEAD_ZONE_BBW_MAX = 0.035
DEAD_ZONE_VOL_RATIO_MAX = 0.42
BREAKOUT_MIN_VOL_RATIO = 1.00
BREAKOUT_MIN_ADX = 18.5
BREAKOUT_MAX_WICK_RATIO = 0.40
BREAKOUT_MIN_BODY_TO_RANGE = 0.33
BREAKOUT_MIN_RANGE_EXPANSION = 1.03
LATE_EXPANSION_RSI_15M_LONG = 78.0
LATE_EXPANSION_RSI_15M_SHORT = 22.0
LATE_EXPANSION_RSI_15M_HARD_LONG = 82.0
LATE_EXPANSION_RSI_15M_HARD_SHORT = 18.0
LATE_EXPANSION_DIST_EMA20 = 0.028
LATE_EXPANSION_DIST_EMA50 = 0.045
LATE_EXPANSION_ATR_MULT = 2.0
ANTI_CHASE_RSI_15M_LONG = 82.0
ANTI_CHASE_RSI_15M_SHORT = 18.0
ANTI_CHASE_MTF_RSI_5M_LONG = 72.0
ANTI_CHASE_MTF_RSI_5M_SHORT = 28.0
ANTI_CHASE_DIST_EMA20_15M = 0.025
M1_EMA_FAST = 9
M1_EMA_SLOW = 21
M1_RSI_PERIOD = 14
M1_LONG_STRETCH_WARN = 0.0040
M1_LONG_STRETCH_HARD = 0.0070
M1_SHORT_STRETCH_WARN = -0.0040
M1_SHORT_STRETCH_HARD = -0.0070
M1_RSI_LONG_WARN = 72.0
M1_RSI_LONG_HARD = 78.0
M1_RSI_SHORT_WARN = 28.0
M1_RSI_SHORT_HARD = 22.0
M1_SPIKE_BODY_ATR_MULT = 2.0
M1_SPIKE_WICK_RATIO = 0.60
M1_SPIKE_COMBO_PENALTY = 35.0
PENALTY_M1_STRETCH_WARN = 14.0
PENALTY_M1_STRETCH_HARD = 28.0
PENALTY_M1_RSI_WARN = 12.0
PENALTY_M1_RSI_HARD = 25.0
PENALTY_LATE_EXPANSION_SOFT = 18.0
PENALTY_ANTI_FOMO_EXTREME = 28.0
CONF_PENALTY_STRETCH_WARN = 0.06
CONF_PENALTY_STRETCH_HARD = 0.14
CONF_PENALTY_RSI_WARN = 0.05
CONF_PENALTY_RSI_HARD = 0.12
CONF_PENALTY_LATE_EXPANSION = 0.08
MIN_HYBRID_CONFIDENCE = 0.0
SOFT_DELAY_PRIORITY_FLOOR = 0.0


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        if isinstance(value, str) and value.strip() == "":
            return default
        return float(value)
    except Exception:
        return default


def _safe_str(value: Any, default: str = "") -> str:
    try:
        if value is None:
            return default
        return str(value)
    except Exception:
        return default


def _quality_rank_bonus(entry_quality: Any) -> float:
    q = _safe_str(entry_quality, "D").upper()
    if q == "A+":
        return 2.0
    if q == "A":
        return 0.75
    return 0.0


def _upper(value: Any, default: str = "") -> str:
    return _safe_str(value, default).upper()


def _clip(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _get_series(df: pd.DataFrame, candidates: List[str]) -> Optional[pd.Series]:
    for col in candidates:
        if col in df.columns:
            return pd.to_numeric(df[col], errors="coerce")
    return None


def _get_close(df: pd.DataFrame) -> Optional[pd.Series]:
    return _get_series(df, ["close", "Close", "c"])


def _get_high(df: pd.DataFrame) -> Optional[pd.Series]:
    return _get_series(df, ["high", "High", "h"])


def _get_low(df: pd.DataFrame) -> Optional[pd.Series]:
    return _get_series(df, ["low", "Low", "l"])


def _get_open(df: pd.DataFrame) -> Optional[pd.Series]:
    return _get_series(df, ["open", "Open", "o"])


def _ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False, min_periods=max(2, span // 2)).mean()


def _rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    return rsi.bfill().fillna(50.0)


def _true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    prev_close = close.shift(1)
    ranges = pd.concat([(high - low).abs(), (high - prev_close).abs(), (low - prev_close).abs()], axis=1)
    return ranges.max(axis=1)


def _atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high = _get_high(df)
    low = _get_low(df)
    close = _get_close(df)
    if high is None or low is None or close is None or len(df) < period + 2:
        return pd.Series([np.nan] * len(df), index=df.index)
    tr = _true_range(high, low, close)
    return tr.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


def _adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high = _get_high(df)
    low = _get_low(df)
    close = _get_close(df)
    if high is None or low is None or close is None or len(df) < period + 5:
        return pd.Series([np.nan] * len(df), index=df.index)
    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = pd.Series(np.where((up_move > down_move) & (up_move > 0), up_move, 0.0), index=df.index)
    minus_dm = pd.Series(np.where((down_move > up_move) & (down_move > 0), down_move, 0.0), index=df.index)
    tr = _true_range(high, low, close)
    atr = tr.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    plus_di = 100 * plus_dm.ewm(alpha=1 / period, adjust=False, min_periods=period).mean() / atr.replace(0, np.nan)
    minus_di = 100 * minus_dm.ewm(alpha=1 / period, adjust=False, min_periods=period).mean() / atr.replace(0, np.nan)
    dx = ((plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)) * 100
    return dx.ewm(alpha=1 / period, adjust=False, min_periods=period).mean().bfill().fillna(10.0)


def _bollinger_bandwidth(close: pd.Series, period: int = 20, num_std: float = 2.0) -> pd.Series:
    ma = close.rolling(period, min_periods=period).mean()
    std = close.rolling(period, min_periods=period).std()
    upper = ma + num_std * std
    lower = ma - num_std * std
    bbw = (upper - lower) / ma.replace(0, np.nan)
    return bbw.bfill().fillna(0.02)


def _last(series: pd.Series, default: float = 0.0) -> float:
    try:
        if series is None or len(series) == 0:
            return default
        value = series.iloc[-1]
        if pd.isna(value):
            return default
        return float(value)
    except Exception:
        return default


def _candle_stats(df: pd.DataFrame) -> Dict[str, float]:
    o = _get_open(df)
    h = _get_high(df)
    l = _get_low(df)
    c = _get_close(df)
    if o is None or h is None or l is None or c is None or len(df) == 0:
        return {"body": 0.0, "range": 0.0, "upper_wick": 0.0, "lower_wick": 0.0, "body_to_range": 0.0, "upper_wick_ratio": 0.0, "lower_wick_ratio": 0.0}
    o1, h1, l1, c1 = float(o.iloc[-1]), float(h.iloc[-1]), float(l.iloc[-1]), float(c.iloc[-1])
    body = abs(c1 - o1)
    rng = max(h1 - l1, 1e-9)
    upper_wick = max(h1 - max(o1, c1), 0.0)
    lower_wick = max(min(o1, c1) - l1, 0.0)
    return {"body": body, "range": rng, "upper_wick": upper_wick, "lower_wick": lower_wick, "body_to_range": body / rng, "upper_wick_ratio": upper_wick / rng, "lower_wick_ratio": lower_wick / rng}


def _distance_ratio(price: float, ref_price: float) -> float:
    return 0.0 if ref_price == 0 else (price - ref_price) / ref_price


def _avg_body(df: pd.DataFrame, lookback: int = 12) -> float:
    o = _get_open(df)
    c = _get_close(df)
    if o is None or c is None or len(df) < 2:
        return 0.0
    body = (c - o).abs()
    tail = body.tail(lookback)
    return float(tail.mean()) if len(tail) else 0.0


def _extract_regime(adv: Dict[str, Any], base_signal: Dict[str, Any]) -> str:
    return _upper(adv.get("regime_label", base_signal.get("regime", adv.get("regime", "RANGING"))), "RANGING")


def _extract_direction(base_signal: Dict[str, Any]) -> str:
    return _upper(base_signal.get("direction", "NONE"), "NONE")


def _extract_priority(base_signal: Dict[str, Any]) -> float:
    base_pri = _safe_float(base_signal.get("priority_score", np.nan), np.nan)
    if math.isnan(base_pri):
        base_pri = max(0.0, abs(_safe_float(base_signal.get("score", 0.0))))
    return base_pri


def _extract_confidence(base_signal: Dict[str, Any]) -> float:
    return _clip(_safe_float(base_signal.get("confidence", 0.0)), 0.0, 1.0)


def _extract_score(base_signal: Dict[str, Any]) -> float:
    return _safe_float(base_signal.get("score", 0.0))


def _extract_mtf_value(mtf_result: Optional[Dict[str, Any]], key: str, default: float = 50.0) -> float:
    if not isinstance(mtf_result, dict):
        return default
    return _safe_float(mtf_result.get(key, default), default)


def _macro_ranging_low_conf(direction: str, regime: str, confidence: float) -> Tuple[bool, str]:
    if direction == "NONE":
        return True, ""
    if regime == "RANGING" and confidence < RANGING_LOW_CONF_THRESHOLD:
        return False, f"RANGING_LOW_CONF({confidence:.2f})"
    return True, ""


def _macro_weak_fuel(adv: Dict[str, Any]) -> Tuple[bool, str, Dict[str, float]]:
    oi_15m = _safe_float(adv.get("oi_change_15m_pct", adv.get("oi_15m_pct", 0.0)))
    oi_1h = _safe_float(adv.get("oi_change_1h_pct", adv.get("oi_1h_pct", 0.0)))
    cvd = _safe_float(adv.get("cvd", 0.0))
    vol_ratio = _safe_float(adv.get("vol_ratio", 0.0))
    weak_oi = abs(oi_15m) < WEAK_FUEL_MIN_ABS_OI_15M and abs(oi_1h) < WEAK_FUEL_MIN_ABS_OI_1H
    weak_cvd = abs(cvd) < WEAK_FUEL_MIN_CVD_USD
    weak_vol = vol_ratio < WEAK_FUEL_MIN_VOL_RATIO
    fuel_ok = not (weak_oi and weak_cvd and weak_vol)
    reason = "" if fuel_ok else f"WEAK_FUEL(oi15={oi_15m:+.2f},oi1h={oi_1h:+.2f},cvd={cvd:,.0f},vol={vol_ratio:.2f})"
    return fuel_ok, reason, {"oi_15m": oi_15m, "oi_1h": oi_1h, "cvd": cvd, "vol_ratio": vol_ratio}


def _macro_dead_zone(adv: Dict[str, Any], df_15m: pd.DataFrame) -> Tuple[bool, str, Dict[str, float]]:
    close = _get_close(df_15m)
    if close is None or len(close) < 25:
        return True, "", {"adx": 20.0, "bbw": 0.05, "vol_ratio": _safe_float(adv.get("vol_ratio", 1.0))}
    adx_val = _last(_adx(df_15m, 14), 20.0)
    bbw_val = _last(_bollinger_bandwidth(close, 20, 2.0), 0.05)
    vol_ratio = _safe_float(adv.get("vol_ratio", 1.0))
    is_dead = adx_val <= DEAD_ZONE_ADX_MAX and bbw_val <= DEAD_ZONE_BBW_MAX and vol_ratio <= DEAD_ZONE_VOL_RATIO_MAX
    reason = f"DEAD_ZONE(ADX:{adx_val:.1f},BBW:{bbw_val:.3f},VOL:{vol_ratio:.2f})" if is_dead else ""
    return not is_dead, reason, {"adx": adx_val, "bbw": bbw_val, "vol_ratio": vol_ratio}


def _macro_breakout_validity(direction: str, adv: Dict[str, Any], df_15m: pd.DataFrame) -> Tuple[bool, str, Dict[str, float]]:
    if direction == "NONE":
        return False, "NO_BREAKOUT", {"adx": 0.0, "vol_ratio": 0.0, "body_to_range": 0.0, "wick_ratio": 0.0, "range_expansion": 1.0}
    adx_val = _last(_adx(df_15m, 14), 15.0)
    vol_ratio = _safe_float(adv.get("vol_ratio", 0.0))
    candle = _candle_stats(df_15m)
    h, l, c = _get_high(df_15m), _get_low(df_15m), _get_close(df_15m)
    reasons: List[str] = []
    range_expansion = 1.0
    wick_ratio = candle["upper_wick_ratio"] if direction == "LONG" else candle["lower_wick_ratio"]
    if vol_ratio < BREAKOUT_MIN_VOL_RATIO:
        reasons.append(f"LOW_VOL_BREAKOUT({vol_ratio:.1f})")
    if adx_val < BREAKOUT_MIN_ADX:
        reasons.append(f"WEAK_ADX_BREAKOUT({adx_val:.1f})")
    if h is not None and l is not None and len(df_15m) >= 2:
        current_range = float(h.iloc[-1] - l.iloc[-1])
        prev_range = float(h.iloc[-2] - l.iloc[-2])
        if prev_range > 0:
            range_expansion = current_range / prev_range
        if range_expansion < BREAKOUT_MIN_RANGE_EXPANSION:
            reasons.append(f"WEAK_MOMENTUM_BREAKOUT({range_expansion:.2f})")
    if candle["body_to_range"] < BREAKOUT_MIN_BODY_TO_RANGE:
        reasons.append(f"WEAK_BODY_BREAKOUT({candle['body_to_range']:.2f})")
    if wick_ratio > BREAKOUT_MAX_WICK_RATIO:
        reasons.append(f"WICK_REJECTION({wick_ratio:.2f})")
    if direction == "LONG" and h is not None and c is not None and len(df_15m) >= 2 and float(c.iloc[-1]) <= float(h.iloc[-2]):
        reasons.append("NO_BREAKOUT")
    elif direction == "SHORT" and l is not None and c is not None and len(df_15m) >= 2 and float(c.iloc[-1]) >= float(l.iloc[-2]):
        reasons.append("NO_BREAKOUT")
    is_valid = len(reasons) == 0
    reason = "" if is_valid else "BREAKOUT_INVALID:" + "|".join(reasons)
    return is_valid, reason, {"adx": adx_val, "vol_ratio": vol_ratio, "body_to_range": candle["body_to_range"], "wick_ratio": wick_ratio, "range_expansion": range_expansion}


def _macro_late_expansion(direction: str, df_15m: pd.DataFrame) -> Tuple[bool, str, Dict[str, float]]:
    close = _get_close(df_15m)
    if close is None or len(close) < 55 or direction == "NONE":
        return False, "", {"rsi_15m": 50.0, "dist_ema20": 0.0, "dist_ema50": 0.0, "atr_mult": 0.0, "hard_late": False}
    close_last = float(close.iloc[-1])
    ema20 = float(_last(_ema(close, 20), close_last))
    ema50 = float(_last(_ema(close, 50), close_last))
    rsi15 = float(_last(_rsi(close, 14), 50.0))
    atr15 = float(_last(_atr(df_15m, 14), 0.0))
    dist_ema20 = _distance_ratio(close_last, ema20)
    dist_ema50 = _distance_ratio(close_last, ema50)
    atr_mult = abs(close_last - ema20) / max(atr15, 1e-9) if atr15 > 0 else 0.0
    late = False
    hard_late = False
    if direction == "LONG":
        late = rsi15 >= LATE_EXPANSION_RSI_15M_LONG and dist_ema20 >= LATE_EXPANSION_DIST_EMA20 and (dist_ema50 >= LATE_EXPANSION_DIST_EMA50 or atr_mult >= LATE_EXPANSION_ATR_MULT)
        hard_late = rsi15 >= LATE_EXPANSION_RSI_15M_HARD_LONG and dist_ema20 >= ANTI_CHASE_DIST_EMA20_15M
    elif direction == "SHORT":
        late = rsi15 <= LATE_EXPANSION_RSI_15M_SHORT and dist_ema20 <= -LATE_EXPANSION_DIST_EMA20 and (dist_ema50 <= -LATE_EXPANSION_DIST_EMA50 or atr_mult >= LATE_EXPANSION_ATR_MULT)
        hard_late = rsi15 <= LATE_EXPANSION_RSI_15M_HARD_SHORT and dist_ema20 <= -ANTI_CHASE_DIST_EMA20_15M
    if hard_late:
        reason = f"HARD_LATE_EXPANSION(rsi15={rsi15:.1f},d20={dist_ema20:+.3f},d50={dist_ema50:+.3f},atrx={atr_mult:.2f})"
    elif late:
        reason = f"LATE_EXPANSION(rsi15={rsi15:.1f},d20={dist_ema20:+.3f},d50={dist_ema50:+.3f},atrx={atr_mult:.2f})"
    else:
        reason = ""
    return late or hard_late, reason, {"rsi_15m": rsi15, "dist_ema20": dist_ema20, "dist_ema50": dist_ema50, "atr_mult": atr_mult, "hard_late": hard_late}


def _macro_anti_chase(direction: str, df_15m: pd.DataFrame, mtf_result: Optional[Dict[str, Any]]) -> Tuple[bool, str, Dict[str, float]]:
    close_15m = _get_close(df_15m)
    if close_15m is None or len(close_15m) < 25 or direction == "NONE":
        return True, "", {"rsi_1m": 50.0, "rsi_5m": 50.0, "rsi_15m": 50.0, "dist_ema20_15m": 0.0}
    close_last = float(close_15m.iloc[-1])
    ema20_15m = float(_last(_ema(close_15m, 20), close_last))
    dist_ema20_15m = _distance_ratio(close_last, ema20_15m)
    rsi_15m = float(_last(_rsi(close_15m, 14), 50.0))
    rsi_1m = _extract_mtf_value(mtf_result, "rsi_1m", 50.0)
    rsi_5m = _extract_mtf_value(mtf_result, "rsi_5m", 50.0)
    if direction == "LONG":
        hard_reject = rsi_15m >= ANTI_CHASE_RSI_15M_LONG and rsi_5m >= ANTI_CHASE_MTF_RSI_5M_LONG and dist_ema20_15m >= ANTI_CHASE_DIST_EMA20_15M
        if hard_reject:
            return False, f"ANTI_CHASE_LONG(rsi1={rsi_1m:.1f},rsi5={rsi_5m:.1f},rsi15={rsi_15m:.1f},d15={dist_ema20_15m:+.3f})", {"rsi_1m": rsi_1m, "rsi_5m": rsi_5m, "rsi_15m": rsi_15m, "dist_ema20_15m": dist_ema20_15m}
    else:
        hard_reject = rsi_15m <= ANTI_CHASE_RSI_15M_SHORT and rsi_5m <= ANTI_CHASE_MTF_RSI_5M_SHORT and dist_ema20_15m <= -ANTI_CHASE_DIST_EMA20_15M
        if hard_reject:
            return False, f"ANTI_CHASE_SHORT(rsi1={rsi_1m:.1f},rsi5={rsi_5m:.1f},rsi15={rsi_15m:.1f},d15={dist_ema20_15m:+.3f})", {"rsi_1m": rsi_1m, "rsi_5m": rsi_5m, "rsi_15m": rsi_15m, "dist_ema20_15m": dist_ema20_15m}
    return True, "", {"rsi_1m": rsi_1m, "rsi_5m": rsi_5m, "rsi_15m": rsi_15m, "dist_ema20_15m": dist_ema20_15m}


def _micro_timing(direction: str, df_1m: pd.DataFrame) -> Dict[str, Any]:
    result = {"micro_timing_state": "OK", "entry_delay": False, "timing_penalty": 0.0, "confidence_penalty": 0.0, "hard_veto": False, "hard_veto_reason": "", "ai_flags": [], "anti_fomo_penalty": 0.0, "m1_rsi": 50.0, "m1_stretch": 0.0}
    if direction == "NONE":
        return result
    close = _get_close(df_1m)
    if close is None or len(close) < max(M1_EMA_SLOW + 5, 30):
        return result
    close_last = float(close.iloc[-1])
    ema_fast = float(_last(_ema(close, M1_EMA_FAST), close_last))
    rsi1 = float(_last(_rsi(close, M1_RSI_PERIOD), 50.0))
    atr1 = float(_last(_atr(df_1m, 14), 0.0))
    candle = _candle_stats(df_1m)
    avg_body = _avg_body(df_1m, 12)
    stretch = _distance_ratio(close_last, ema_fast)
    result["m1_rsi"] = rsi1
    result["m1_stretch"] = stretch
    if direction == "LONG":
        if stretch >= M1_LONG_STRETCH_HARD:
            result["timing_penalty"] += PENALTY_M1_STRETCH_HARD
            result["confidence_penalty"] += CONF_PENALTY_STRETCH_HARD
            result["entry_delay"] = True
            result["ai_flags"].append(f"M1_STRETCHED_HARD({stretch:+.3f})")
        elif stretch >= M1_LONG_STRETCH_WARN:
            result["timing_penalty"] += PENALTY_M1_STRETCH_WARN
            result["confidence_penalty"] += CONF_PENALTY_STRETCH_WARN
            result["entry_delay"] = True
            result["ai_flags"].append(f"M1_STRETCHED({stretch:+.3f})")
        if rsi1 >= M1_RSI_LONG_HARD:
            result["timing_penalty"] += PENALTY_M1_RSI_HARD
            result["confidence_penalty"] += CONF_PENALTY_RSI_HARD
            result["entry_delay"] = True
            result["ai_flags"].append(f"M1_RSI_HOT_HARD({rsi1:.1f})")
        elif rsi1 >= M1_RSI_LONG_WARN:
            result["timing_penalty"] += PENALTY_M1_RSI_WARN
            result["confidence_penalty"] += CONF_PENALTY_RSI_WARN
            result["entry_delay"] = True
            result["ai_flags"].append(f"M1_RSI_HOT({rsi1:.1f})")
    else:
        if stretch <= M1_SHORT_STRETCH_HARD:
            result["timing_penalty"] += PENALTY_M1_STRETCH_HARD
            result["confidence_penalty"] += CONF_PENALTY_STRETCH_HARD
            result["entry_delay"] = True
            result["ai_flags"].append(f"M1_STRETCHED_HARD({stretch:+.3f})")
        elif stretch <= M1_SHORT_STRETCH_WARN:
            result["timing_penalty"] += PENALTY_M1_STRETCH_WARN
            result["confidence_penalty"] += CONF_PENALTY_STRETCH_WARN
            result["entry_delay"] = True
            result["ai_flags"].append(f"M1_STRETCHED({stretch:+.3f})")
        if rsi1 <= M1_RSI_SHORT_HARD:
            result["timing_penalty"] += PENALTY_M1_RSI_HARD
            result["confidence_penalty"] += CONF_PENALTY_RSI_HARD
            result["entry_delay"] = True
            result["ai_flags"].append(f"M1_RSI_COLD_HARD({rsi1:.1f})")
        elif rsi1 <= M1_RSI_SHORT_WARN:
            result["timing_penalty"] += PENALTY_M1_RSI_WARN
            result["confidence_penalty"] += CONF_PENALTY_RSI_WARN
            result["entry_delay"] = True
            result["ai_flags"].append(f"M1_RSI_COLD({rsi1:.1f})")
    body = candle["body"]
    body_extreme = body >= max(avg_body * M1_SPIKE_BODY_ATR_MULT, atr1 * 0.8, 1e-9)
    wick_ratio = max(candle["upper_wick_ratio"], candle["lower_wick_ratio"])
    wick_extreme = wick_ratio >= M1_SPIKE_WICK_RATIO
    combo_hot = (stretch >= M1_LONG_STRETCH_HARD and rsi1 >= M1_RSI_LONG_HARD) if direction == "LONG" else (stretch <= M1_SHORT_STRETCH_HARD and rsi1 <= M1_RSI_SHORT_HARD)
    if body_extreme and wick_extreme and combo_hot:
        result["hard_veto"] = True
        result["hard_veto_reason"] = f"M1_SPIKE_RISK(stretch={stretch:+.3f},rsi1={rsi1:.1f},wick={wick_ratio:.2f})"
        result["timing_penalty"] += M1_SPIKE_COMBO_PENALTY
        result["confidence_penalty"] += 0.18
        result["ai_flags"].append(result["hard_veto_reason"])
    result["micro_timing_state"] = "SPIKE_RISK" if result["hard_veto"] else ("DELAY" if result["entry_delay"] else "OK")
    result["anti_fomo_penalty"] = float(result["timing_penalty"])
    return result


def rank_signal_hybrid(*, symbol: str, adv: Dict[str, Any], mtf_result: Optional[Dict[str, Any]], df_15m: pd.DataFrame, df_1m: pd.DataFrame, base_signal: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    if not isinstance(base_signal, dict):
        base_signal = {}
    else:
        base_signal = dict(base_signal)
    direction = _extract_direction(base_signal)
    base_score = _extract_score(base_signal)
    base_conf = _extract_confidence(base_signal)
    base_priority = _extract_priority(base_signal)
    regime = _extract_regime(adv, base_signal)
    out: Dict[str, Any] = dict(base_signal)
    out["symbol"] = symbol
    out["regime"] = regime
    ai_flags: List[str] = []
    macro_pass = True
    veto_reason = ""
    ok_ranging, reason_ranging = _macro_ranging_low_conf(direction, regime, base_conf)
    if not ok_ranging:
        macro_pass = False
        veto_reason = reason_ranging
        ai_flags.append(f"VETO:{reason_ranging}")
    fuel_ok, fuel_reason, fuel_stats = _macro_weak_fuel(adv)
    if macro_pass and not fuel_ok:
        macro_pass = False
        veto_reason = fuel_reason
        ai_flags.append(f"VETO:{fuel_reason}")
    dead_ok, dead_reason, dead_stats = _macro_dead_zone(adv, df_15m)
    if macro_pass and not dead_ok:
        macro_pass = False
        veto_reason = dead_reason
        ai_flags.append(dead_reason)
    breakout_valid, breakout_reason, breakout_stats = _macro_breakout_validity(direction, adv, df_15m)
    if macro_pass and not breakout_valid:
        macro_pass = False
        veto_reason = breakout_reason
        ai_flags.append(breakout_reason)
    late_expansion, late_reason, late_stats = _macro_late_expansion(direction, df_15m)
    if macro_pass and late_expansion and late_stats.get("hard_late", False):
        macro_pass = False
        veto_reason = late_reason
        ai_flags.append(f"VETO:{late_reason}")
    anti_chase_ok, anti_chase_reason, anti_chase_stats = _macro_anti_chase(direction, df_15m, mtf_result)
    if macro_pass and not anti_chase_ok:
        macro_pass = False
        veto_reason = anti_chase_reason
        ai_flags.append(f"VETO:{anti_chase_reason}")
    if not macro_pass:
        quality_bonus = _quality_rank_bonus(base_signal.get("entry_quality", out.get("entry_quality", "D")))
        out.update({
            "hybrid_direction": "NONE",
            "hybrid_score": 0.0,
            "hybrid_confidence": 0.0,
            "hybrid_priority_score": 0.0,
            "direction": "NONE",
            "score": 0.0,
            "confidence": 0.0,
            "priority_score": 0.0,
            "ai_flags": ai_flags,
            "veto_reason": veto_reason,
            "macro_pass": False,
            "macro_reason": veto_reason,
            "micro_timing_state": "SKIPPED",
            "entry_delay": False,
            "timing_penalty": 0.0,
            "anti_fomo_penalty": 0.0,
            "fuel_ok": fuel_ok,
            "breakout_valid": False,
            "late_expansion": late_expansion,
            "oi_15m": fuel_stats["oi_15m"],
            "oi_1h": fuel_stats["oi_1h"],
            "cvd": fuel_stats["cvd"],
            "vol_ratio": fuel_stats["vol_ratio"],
            "adx": dead_stats["adx"],
            "bbw": dead_stats["bbw"],
            "rsi_1m_ctx": anti_chase_stats["rsi_1m"],
            "rsi_5m_ctx": anti_chase_stats["rsi_5m"],
            "rsi_15m_ctx": anti_chase_stats["rsi_15m"],
            "quality_rank_bonus": quality_bonus,
            "hybrid_meta": {
                "breakout_valid": False,
                "veto_reason": veto_reason,
                "anti_fomo_penalty": 0.0,
                "timing_penalty": 0.0,
                "quality_rank_bonus": quality_bonus,
                "base_entry_quality": _safe_str(base_signal.get("entry_quality", out.get("entry_quality", "D")), "D"),
                "ai_flags": list(ai_flags),
            },
        })
        return out
    hybrid_direction = direction
    hybrid_score = base_score
    hybrid_conf = base_conf
    hybrid_priority = base_priority
    quality_bonus = _quality_rank_bonus(base_signal.get("entry_quality", out.get("entry_quality", "D")))
    if late_expansion:
        hybrid_priority -= PENALTY_LATE_EXPANSION_SOFT
        hybrid_conf -= CONF_PENALTY_LATE_EXPANSION
        ai_flags.append(late_reason)
    rsi_15m_ctx = anti_chase_stats.get("rsi_15m", 50.0)
    if hybrid_direction == "LONG" and rsi_15m_ctx >= 78.0:
        hybrid_priority -= PENALTY_ANTI_FOMO_EXTREME
        hybrid_conf -= 0.10
        ai_flags.append(f"ANTI_FOMO_LONG({rsi_15m_ctx:.1f})")
    elif hybrid_direction == "SHORT" and rsi_15m_ctx <= 22.0:
        hybrid_priority -= PENALTY_ANTI_FOMO_EXTREME
        hybrid_conf -= 0.10
        ai_flags.append(f"ANTI_FOMO_SHORT({rsi_15m_ctx:.1f})")
    micro_1m = _micro_timing(hybrid_direction, df_1m)
    if micro_1m["hard_veto"]:
        ai_flags.extend([flag for flag in micro_1m["ai_flags"] if flag not in ai_flags])
        veto_reason = micro_1m["hard_veto_reason"]
        out.update({
            "hybrid_direction": "NONE",
            "hybrid_score": 0.0,
            "hybrid_confidence": 0.0,
            "hybrid_priority_score": 0.0,
            "direction": "NONE",
            "score": 0.0,
            "confidence": 0.0,
            "priority_score": 0.0,
            "ai_flags": ai_flags,
            "veto_reason": veto_reason,
            "macro_pass": True,
            "macro_reason": "OK",
            "micro_timing_state": micro_1m["micro_timing_state"],
            "entry_delay": True,
            "timing_penalty": micro_1m["timing_penalty"],
            "anti_fomo_penalty": micro_1m["anti_fomo_penalty"],
            "fuel_ok": fuel_ok,
            "breakout_valid": breakout_valid,
            "late_expansion": late_expansion,
            "oi_15m": fuel_stats["oi_15m"],
            "oi_1h": fuel_stats["oi_1h"],
            "cvd": fuel_stats["cvd"],
            "vol_ratio": fuel_stats["vol_ratio"],
            "adx": dead_stats["adx"],
            "bbw": dead_stats["bbw"],
            "quality_rank_bonus": quality_bonus,
            "hybrid_meta": {
                "breakout_valid": breakout_valid,
                "veto_reason": veto_reason,
                "anti_fomo_penalty": float(micro_1m["anti_fomo_penalty"]),
                "timing_penalty": float(micro_1m["timing_penalty"]),
                "quality_rank_bonus": quality_bonus,
                "base_entry_quality": _safe_str(base_signal.get("entry_quality", out.get("entry_quality", "D")), "D"),
                "ai_flags": list(ai_flags),
            },
        })
        return out
    hybrid_priority -= float(micro_1m["timing_penalty"])
    hybrid_priority += quality_bonus
    hybrid_conf -= float(micro_1m["confidence_penalty"])
    hybrid_conf = _clip(hybrid_conf, MIN_HYBRID_CONFIDENCE, 1.0)
    hybrid_score += -0.55 * float(micro_1m["timing_penalty"])
    for flag in micro_1m["ai_flags"]:
        if flag not in ai_flags:
            ai_flags.append(flag)
    hybrid_priority = max(SOFT_DELAY_PRIORITY_FLOOR, hybrid_priority)
    phase1_profile = {
        "mode": "TRENDING" if regime == "TRENDING" else ("RANGING" if regime == "RANGING" else "DEFAULT"),
        "partial_tp_r": 0.8 if regime == "TRENDING" else (0.5 if regime == "RANGING" else 0.6),
        "full_tp_r": 1.5 if regime == "TRENDING" else (0.95 if regime == "RANGING" else 1.1),
        "partial_close_fraction": 0.50 if regime == "TRENDING" else (0.60 if regime == "RANGING" else 0.55),
        "max_hold_minutes": 90 if regime == "TRENDING" else (60 if regime == "RANGING" else 75),
        "move_sl_to_be_after_partial": True,
        "allow_runner": False,
    }
    out.update({
        "hybrid_direction": hybrid_direction,
        "hybrid_score": float(hybrid_score),
        "hybrid_confidence": float(hybrid_conf),
        "hybrid_priority_score": float(hybrid_priority),
        "direction": hybrid_direction,
        "score": float(hybrid_score),
        "confidence": float(hybrid_conf),
        "priority_score": float(hybrid_priority),
        "ai_flags": ai_flags,
        "veto_reason": "",
        "macro_pass": True,
        "macro_reason": "OK",
        "micro_timing_state": "DELAY" if micro_1m["entry_delay"] else "OK",
        "entry_delay": bool(micro_1m["entry_delay"]),
        "timing_penalty": float(micro_1m["timing_penalty"]),
        "anti_fomo_penalty": float(micro_1m["anti_fomo_penalty"]),
        "fuel_ok": fuel_ok,
        "breakout_valid": breakout_valid,
        "late_expansion": late_expansion,
        "oi_15m": fuel_stats["oi_15m"],
        "oi_1h": fuel_stats["oi_1h"],
        "cvd": fuel_stats["cvd"],
        "vol_ratio": fuel_stats["vol_ratio"],
        "adx": breakout_stats["adx"],
        "bbw": dead_stats["bbw"],
        "m1_rsi": micro_1m["m1_rsi"],
        "m1_stretch": micro_1m["m1_stretch"],
        "rsi_15m_ctx": anti_chase_stats["rsi_15m"],
        "rsi_5m_ctx": anti_chase_stats["rsi_5m"],
        "rsi_1m_ctx": anti_chase_stats["rsi_1m"],
        "phase1_profile": phase1_profile,
        "quality_rank_bonus": quality_bonus,
        "hybrid_meta": {
            "breakout_valid": breakout_valid,
            "veto_reason": "",
            "anti_fomo_penalty": float(micro_1m["anti_fomo_penalty"]),
            "timing_penalty": float(micro_1m["timing_penalty"]),
            "quality_rank_bonus": quality_bonus,
            "base_entry_quality": _safe_str(base_signal.get("entry_quality", out.get("entry_quality", "D")), "D"),
            "ai_flags": list(ai_flags),
        },
    })
    return out