from __future__ import annotations

import time
from typing import Any, Dict

import pandas as pd

_ACC_CACHE: Dict[str, Dict[str, Any]] = {}
_ALERT_CACHE: Dict[str, float] = {}
ACCUMULATION_TTL_SEC = 8 * 3600
ALERT_TTL_SEC = 90 * 60
ALERTABLE_STATES = {'VALID_ACCUMULATION', 'ACCUMULATION_READY'}


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, '', 'None'):
            return default
        return float(value)
    except Exception:
        return default


def _empty(reason: str = 'NONE') -> Dict[str, Any]:
    return {
        'is_accumulating': False,
        'accumulation_state': 'NONE',
        'accumulation_score': 0.0,
        'price_range_pct': 0.0,
        'volume_ratio_vs_baseline': 0.0,
        'bullish_close_pct': 0.0,
        'lower_rejection_pct': 0.0,
        'cvd_support': False,
        'oi_support': False,
        'reason': reason,
    }


def detect_silent_accumulation_v2(
    df: pd.DataFrame,
    adv: Dict[str, Any] | None = None,
    mtf_result: Dict[str, Any] | None = None,
    *,
    lookback: int = 20,
    baseline_bars: int = 50,
) -> Dict[str, Any]:
    adv = adv or {}
    mtf_result = mtf_result or {}
    if df is None or df.empty or len(df) < (lookback + baseline_bars):
        return _empty('INSUFFICIENT_DATA')

    required_cols = {'open', 'high', 'low', 'close', 'volume'}
    if not required_cols.issubset(set(df.columns)):
        return _empty('MISSING_COLUMNS')

    recent_df = df.tail(lookback).copy()
    baseline_df = df.iloc[-lookback - baseline_bars:-lookback].copy()
    if baseline_df.empty:
        return _empty('NO_BASELINE')

    lowest_price = max(_safe_float(recent_df['low'].min(), 0.0), 1e-9)
    highest_price = _safe_float(recent_df['high'].max(), 0.0)
    price_range_pct = ((highest_price - lowest_price) / lowest_price) * 100.0 if lowest_price > 0 else 0.0

    recent_vol = _safe_float(recent_df['volume'].mean(), 0.0)
    baseline_vol = max(_safe_float(baseline_df['volume'].mean(), 0.0), 1e-9)
    volume_ratio_vs_baseline = recent_vol / baseline_vol if baseline_vol > 0 else 0.0

    candle_midpoints = (recent_df['high'] + recent_df['low']) / 2.0
    bullish_close_pct = float((recent_df['close'] > candle_midpoints).mean()) if len(recent_df) > 0 else 0.0

    candle_range = (recent_df['high'] - recent_df['low']).clip(lower=1e-9)
    lower_wick = (recent_df[['open', 'close']].min(axis=1) - recent_df['low']).clip(lower=0.0)
    lower_wick_ratio = lower_wick / candle_range
    lower_rejection_pct = float((lower_wick_ratio >= 0.35).mean()) if len(recent_df) > 0 else 0.0

    trend_15m = str(mtf_result.get('trend_15m', 'NEUTRAL')).upper()
    trend_1m = str(mtf_result.get('trend_1m', 'NEUTRAL')).upper()

    cvd = _safe_float(adv.get('cvd', 0.0), 0.0)
    cvd_z = _safe_float(adv.get('cvd_zscore', 0.0), 0.0)
    oi_15m = _safe_float(adv.get('oi_change_15m_pct', adv.get('oi_15m_pct', 0.0)), 0.0)
    funding = _safe_float(adv.get('funding_rate_pct', adv.get('funding_rate', 0.0)), 0.0)
    vol_ratio = _safe_float(adv.get('vol_ratio', 0.0), 0.0)

    compression_score = 0.0
    if price_range_pct <= 2.2:
        compression_score = 10.0
    elif price_range_pct <= 3.2:
        compression_score = 8.0
    elif price_range_pct <= 4.0:
        compression_score = 5.0

    volume_score = 0.0
    if volume_ratio_vs_baseline >= 1.75:
        volume_score = 8.0
    elif volume_ratio_vs_baseline >= 1.45:
        volume_score = 6.0
    elif volume_ratio_vs_baseline >= 1.20:
        volume_score = 3.0

    absorption_score = 0.0
    if bullish_close_pct >= 0.72:
        absorption_score += 5.0
    elif bullish_close_pct >= 0.62:
        absorption_score += 3.0
    if lower_rejection_pct >= 0.62:
        absorption_score += 6.0
    elif lower_rejection_pct >= 0.48:
        absorption_score += 3.0

    structure_score = 0.0
    if trend_15m in {'DOWN', 'NEUTRAL'} and trend_1m == 'UP':
        structure_score += 3.0
    elif trend_1m == 'UP':
        structure_score += 1.0

    cvd_support = cvd > 0 or cvd_z > 0.10
    oi_support = oi_15m >= -0.10
    micro_score = 0.0
    if cvd_support:
        micro_score += 3.0
    if oi_support:
        micro_score += 2.0
    if abs(funding) <= 0.03:
        micro_score += 1.0
    if 0.05 <= vol_ratio <= 1.20:
        micro_score += 1.0

    score = compression_score + volume_score + absorption_score + structure_score + micro_score

    reason_parts = []
    if compression_score > 0:
        reason_parts.append('TIGHT_RANGE')
    if volume_score > 0:
        reason_parts.append('VOLUME_ABSORPTION')
    if lower_rejection_pct >= 0.48:
        reason_parts.append('LOWER_REJECTION')
    if bullish_close_pct >= 0.62:
        reason_parts.append('BULLISH_CLOSES')
    if cvd_support:
        reason_parts.append('CVD_SUPPORT')
    if oi_support:
        reason_parts.append('OI_SUPPORT')

    # State gating made stricter so VALID/READY are not too loose.
    has_core_compression = compression_score >= 8.0
    has_soft_compression = compression_score >= 5.0
    has_real_absorption = absorption_score >= 6.0
    has_soft_absorption = absorption_score >= 3.0
    has_volume_anomaly = volume_score >= 3.0
    has_strong_volume = volume_score >= 6.0
    has_micro_support = micro_score >= 4.0

    state = 'NONE'
    if (
        score >= 25.0
        and has_core_compression
        and has_micro_support
        and (has_strong_volume or has_real_absorption)
    ):
        state = 'ACCUMULATION_READY'
    elif (
        score >= 18.0
        and has_micro_support
        and (
            (has_core_compression and (has_volume_anomaly or has_soft_absorption))
            or (has_soft_compression and has_strong_volume and has_soft_absorption)
        )
    ):
        state = 'VALID_ACCUMULATION'
    elif (
        score >= 10.0
        and has_micro_support
        and (
            has_soft_compression
            or has_real_absorption
            or (has_soft_absorption and volume_ratio_vs_baseline >= 0.95)
        )
    ):
        state = 'EARLY_ACCUMULATION'

    is_accumulating = state != 'NONE'
    return {
        'is_accumulating': is_accumulating,
        'accumulation_state': state,
        'accumulation_score': float(round(score, 2)),
        'price_range_pct': float(round(price_range_pct, 2)),
        'volume_ratio_vs_baseline': float(round(volume_ratio_vs_baseline, 2)),
        'bullish_close_pct': float(round(bullish_close_pct, 2)),
        'lower_rejection_pct': float(round(lower_rejection_pct, 2)),
        'cvd_support': bool(cvd_support),
        'oi_support': bool(oi_support),
        'reason': '+'.join(reason_parts) if is_accumulating else 'NONE',
    }


def mark_silent_accumulation(symbol: str, payload: Dict[str, Any]) -> None:
    if not symbol or not payload.get('is_accumulating'):
        return
    row = dict(payload)
    row['detected_at'] = time.time()
    _ACC_CACHE[str(symbol).upper()] = row


def is_accumulated_recently(symbol: str, max_age_sec: int = ACCUMULATION_TTL_SEC) -> bool:
    row = _ACC_CACHE.get(str(symbol).upper())
    if not row:
        return False
    return (time.time() - float(row.get('detected_at', 0.0) or 0.0)) < max_age_sec


def get_silent_accumulation_details(symbol: str) -> Dict[str, Any]:
    row = _ACC_CACHE.get(str(symbol).upper())
    if not row:
        return _empty('NOT_FOUND')
    if not is_accumulated_recently(symbol):
        return _empty('EXPIRED')
    return dict(row)


def get_recent_silent_accumulation_coins(max_age_sec: int = ACCUMULATION_TTL_SEC) -> list[str]:
    now = time.time()
    return [sym for sym, row in _ACC_CACHE.items() if (now - float(row.get('detected_at', 0.0) or 0.0)) < max_age_sec]


def get_accumulated_coins(*, include_smc: bool = False, smc_watchlist: list[str] | None = None) -> list[str]:
    acc_list = get_recent_silent_accumulation_coins()
    if not include_smc:
        return list(set(acc_list))
    smc_list = list(smc_watchlist or [])
    return list(set(acc_list + smc_list))


def is_alertable_silent_accumulation_state(state: str) -> bool:
    return str(state or 'NONE').upper() in ALERTABLE_STATES


def should_alert_silent_accumulation(symbol: str, *, min_interval_sec: int = ALERT_TTL_SEC) -> bool:
    symbol = str(symbol).upper()
    details = get_silent_accumulation_details(symbol)
    if not details.get('is_accumulating'):
        return False
    if not is_alertable_silent_accumulation_state(str(details.get('accumulation_state', 'NONE'))):
        return False
    now = time.time()
    last_ts = float(_ALERT_CACHE.get(symbol, 0.0) or 0.0)
    if (now - last_ts) < min_interval_sec:
        return False
    _ALERT_CACHE[symbol] = now
    return True
