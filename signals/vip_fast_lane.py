# signals/vip_fast_lane.py
# FASE 3.6 - DIRECTIONAL VIP ACCUMULATION DISCOVERY + VALIDATION + TRIGGER

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional, Set

from signals.silent_accumulation import ALERTABLE_STATES, get_silent_accumulation_details
from signals.smc_engine import smc_engine
from utils.logger import logger
from whales.redis_db import get_pending_whales

ACCUMULATION_CANDIDATE = 'ACCUMULATION_CANDIDATE'
VIP_CONFIRMED = 'VIP_CONFIRMED'
VIP_TRIGGER_READY = 'VIP_TRIGGER_READY'
VIP_NONE = 'NONE'

BIAS_BULLISH = 'BULLISH'
BIAS_NEUTRAL = 'NEUTRAL'

WEIGHT_WHALE = 36
WEIGHT_SILENT = 30
WEIGHT_SESSION = 18
WEIGHT_DAILY = 18
WEIGHT_SMC = 15

CANDIDATE_THRESHOLD = 45
CONFIRMED_THRESHOLD = 60

MAX_AGE_WHALE = 1800
MAX_AGE_SILENT_ACC = 8 * 3600
MAX_AGE_SMC_BOS = 3600

SESSION_SPECS = {
    'ASIA': {'hour': 1, 'minute': 0},
    'LONDON': {'hour': 8, 'minute': 0},
    'NEWYORK': {'hour': 13, 'minute': 0},
    'DAILY_CLOSE': {'hour': 0, 'minute': 0},
}
SESSION_WINDOW_BEFORE_MIN = 30
SESSION_WINDOW_AFTER_MIN = 45
ROLLOVER_WINDOW_BEFORE_MIN = 30
ROLLOVER_WINDOW_AFTER_MIN = 10


_VIP_FAST_LANE_CACHE: set[str] = set()


def remember_vip_fast_lane_symbols(symbols: Iterable[str]) -> None:
    global _VIP_FAST_LANE_CACHE
    try:
        _VIP_FAST_LANE_CACHE = {str(sym).upper() for sym in (symbols or []) if sym}
    except Exception:
        _VIP_FAST_LANE_CACHE = set()


def clear_vip_fast_lane_symbols() -> None:
    global _VIP_FAST_LANE_CACHE
    _VIP_FAST_LANE_CACHE = set()



def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, '', 'None'):
            return default
        return float(value)
    except Exception:
        return default


def _safe_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {'1', 'true', 'yes', 'y', 'on'}
    return bool(value)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _within_minutes(now: datetime, anchor: datetime, before_min: int, after_min: int) -> bool:
    return (anchor - timedelta(minutes=before_min)) <= now <= (anchor + timedelta(minutes=after_min))


def get_active_session_windows(now: datetime | None = None) -> List[str]:
    now = now or _utc_now()
    active: List[str] = []
    for name, spec in SESSION_SPECS.items():
        anchor = now.replace(hour=spec['hour'], minute=spec['minute'], second=0, microsecond=0)
        if _within_minutes(now, anchor, SESSION_WINDOW_BEFORE_MIN, SESSION_WINDOW_AFTER_MIN):
            active.append(name)
    return active


def is_session_window_active(now: datetime | None = None) -> bool:
    return bool(get_active_session_windows(now))


def is_daily_rollover_window_active(now: datetime | None = None) -> bool:
    now = now or _utc_now()
    anchor = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if _within_minutes(now, anchor, ROLLOVER_WINDOW_BEFORE_MIN, ROLLOVER_WINDOW_AFTER_MIN):
        return True
    next_anchor = anchor + timedelta(days=1)
    return _within_minutes(now, next_anchor, ROLLOVER_WINDOW_BEFORE_MIN, ROLLOVER_WINDOW_AFTER_MIN)


def _fresh_iso_age_seconds(iso_text: str) -> float:
    try:
        return time.time() - datetime.fromisoformat(str(iso_text).replace('Z', '+00:00')).timestamp()
    except Exception:
        return 10 ** 9


def _whale_is_bullish(whale: Dict[str, Any]) -> bool:
    source = str(whale.get('source', '')).lower()
    direction = str(whale.get('direction', 'BUY')).upper()
    if source == 'onchain':
        return True
    return direction == 'BUY'


def get_recent_bullish_whale_alert(symbol: str, max_age_sec: int = MAX_AGE_WHALE) -> Dict[str, Any]:
    target = str(symbol or '').upper()
    freshest: Dict[str, Any] = {}
    best_age = float(max_age_sec) + 1.0
    try:
        for whale in get_pending_whales():
            if str(whale.get('symbol', '')).upper() != target:
                continue
            if not _whale_is_bullish(whale):
                continue
            age = _fresh_iso_age_seconds(str(whale.get('detected_at', '')))
            if age <= max_age_sec and age < best_age:
                freshest = dict(whale)
                best_age = age
    except Exception as exc:
        logger.debug('VIP whale check error for %s: %s', target, exc)
    return freshest


def has_recent_whale_alert(symbol: str, max_age_sec: int = MAX_AGE_WHALE) -> bool:
    return bool(get_recent_bullish_whale_alert(symbol, max_age_sec=max_age_sec))


def _silent_state_grade(details: Dict[str, Any] | None) -> str:
    row = dict(details or {})
    state = str(row.get('accumulation_state', 'NONE')).upper()
    if state == 'ACCUMULATION_READY':
        return 'READY'
    if state in ALERTABLE_STATES:
        return 'VALID'
    if state == 'EARLY_ACCUMULATION':
        return 'EARLY'
    return 'NONE'


def has_recent_silent_accumulation(symbol: str, max_age_sec: int = MAX_AGE_SILENT_ACC, *, min_grade: str = 'VALID') -> bool:
    details = get_silent_accumulation_details(symbol)
    if not details.get('is_accumulating'):
        return False
    detected_at = _safe_float(details.get('detected_at'), 0.0)
    if detected_at <= 0:
        return False
    if (time.time() - detected_at) > max_age_sec:
        return False
    grade = _silent_state_grade(details)
    thresholds = {'NONE': 0, 'EARLY': 1, 'VALID': 2, 'READY': 3}
    return thresholds.get(grade, 0) >= thresholds.get(str(min_grade).upper(), 2)


def has_recent_smc_accumulation_context(symbol: str, max_age_sec: int = MAX_AGE_SMC_BOS) -> bool:
    watchlist = smc_engine.get_ob_watchlist() if hasattr(smc_engine, 'get_ob_watchlist') else []
    if symbol not in watchlist:
        return False
    bos_time = _safe_float(getattr(smc_engine, 'ob_watchlist', {}).get(symbol, 0), 0.0)
    if bos_time <= 0:
        return False
    return (time.time() - bos_time) <= max_age_sec


def _smc_context_supports_bullish(market_context: Dict[str, Any] | None = None) -> bool:
    ctx = dict(market_context or {})
    return bool(
        _safe_bool(ctx.get('near_demand_zone'))
        or _safe_bool(ctx.get('has_fresh_bull_ob'))
        or _safe_bool(ctx.get('has_fresh_bull_fvg'))
        or str(ctx.get('structure_bias', 'NEUTRAL')).upper() == 'BULLISH'
        or str(ctx.get('last_bos_dir', 'NONE')).upper() == 'LONG'
    )


def _resolve_discovery_status(*, whale: bool, silent_grade: str, session: bool, daily: bool, smc: bool, score: float) -> str:
    """
    VIP State Machine (sesuai desain):
    1. WHALE_SEEN (Telegram) + DATA_ACCUM (silent) = VIP_CONFIRMED
    2. DATA_ACCUM READY + SMC + SESSION/DAILY = VIP_CONFIRMED (tanpa whale jika data sangat kuat)
    3. WHALE + SMC + (SESSION atau DAILY) = ACCUMULATION_CANDIDATE
    4. DATA_ACCUM VALID + SMC + (SESSION atau DAILY) = ACCUMULATION_CANDIDATE
    
    Prinsip: Tidak boleh jadi VIP tanpa bukti akumulasi nyata (whale atau silent_grade >= VALID)
    """
    has_real_accumulation = whale or silent_grade in {'VALID', 'READY'}
    has_timing = session or daily
    
    # Tier 1: VIP_CONFIRMED - bukti kuat dari multiple sources
    if whale and silent_grade in {'VALID', 'READY'}:
        return VIP_CONFIRMED
    if silent_grade == 'READY' and smc and has_timing:
        return VIP_CONFIRMED
    if whale and smc and has_timing and score >= CONFIRMED_THRESHOLD:
        return VIP_CONFIRMED
    
    # Tier 2: ACCUMULATION_CANDIDATE - ada bukti tapi belum lengkap
    if has_real_accumulation and smc and has_timing:
        return ACCUMULATION_CANDIDATE
    if whale and smc and score >= CANDIDATE_THRESHOLD:
        return ACCUMULATION_CANDIDATE
    if silent_grade == 'READY' and (smc or has_timing):
        return ACCUMULATION_CANDIDATE
    
    return VIP_NONE


def compute_accumulation_score(symbol: str, market_context: Dict[str, Any] | None = None, now: datetime | None = None) -> Dict[str, Any]:
    now = now or _utc_now()
    details = get_silent_accumulation_details(symbol)
    silent_grade = _silent_state_grade(details)
    tg = has_recent_whale_alert(symbol)
    sa = has_recent_silent_accumulation(symbol, min_grade='VALID')
    sess = is_session_window_active(now)
    daily = is_daily_rollover_window_active(now)
    smc = has_recent_smc_accumulation_context(symbol) or _smc_context_supports_bullish(market_context)

    score = 0
    sources: List[str] = []
    if tg:
        score += WEIGHT_WHALE
        sources.append('TELEGRAM_WHALE')
    if sa:
        score += WEIGHT_SILENT
        sources.append(f"SILENT_{str(details.get('accumulation_state', 'VALID')).upper()}")
    if sess:
        score += WEIGHT_SESSION
        active_sessions = get_active_session_windows(now)
        sources.extend([f'SESSION_{name}' for name in active_sessions] or ['SESSION_WINDOW'])
    if daily:
        score += WEIGHT_DAILY
        sources.append('DAILY_ROLLOVER')
    if smc:
        score += WEIGHT_SMC
        sources.append('SMC_CONTEXT')

    status = _resolve_discovery_status(
        whale=tg,
        silent_grade=silent_grade,
        session=sess,
        daily=daily,
        smc=smc,
        score=float(score),
    )

    bias = BIAS_BULLISH if status != VIP_NONE else BIAS_NEUTRAL
    logger.info(
        '[VIP_DISCOVERY] %s | tg=%s sa=%s grade=%s sess=%s daily=%s smc=%s | score=%s | status=%s | bias=%s',
        symbol,
        1 if tg else 0,
        1 if sa else 0,
        silent_grade,
        1 if sess else 0,
        1 if daily else 0,
        1 if smc else 0,
        score,
        status,
        bias,
    )
    return {
        'symbol': str(symbol).upper(),
        'vip_status': status,
        'vip_directional_bias': bias,
        'accumulation_score': float(score),
        'accumulation_sources': sources,
        'is_vip': status in {VIP_CONFIRMED, VIP_TRIGGER_READY},
    }


def evaluate_vip_trigger_ready(
    *,
    symbol: str,
    vip_status: str,
    adv: Dict[str, Any] | None = None,
    signal_payload: Dict[str, Any] | None = None,
    market_context: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    adv = dict(adv or {})
    signal_payload = dict(signal_payload or {})
    market_context = dict(market_context or {})
    if vip_status != VIP_CONFIRMED:
        return {'vip_trigger_ready': False, 'trigger_reason': 'BASE_STATUS_NOT_CONFIRMED'}

    direction = str(signal_payload.get('direction', 'NONE')).upper()
    if direction != 'LONG':
        return {'vip_trigger_ready': False, 'trigger_reason': 'BULLISH_ACCUMULATION_LONG_ONLY'}
    breakout_valid = _safe_bool(signal_payload.get('breakout_valid')) or _safe_bool(signal_payload.get('hybrid_meta', {}).get('breakout_valid'))
    reclaim_valid = str(market_context.get('last_bos_dir', 'NONE')).upper() == 'LONG' and (
        _safe_bool(market_context.get('trendline_retest_valid_long')) or _safe_bool(market_context.get('valid_retest_long'))
    )
    compression_release = _safe_bool(signal_payload.get('fuel_ok')) and _safe_float(adv.get('vol_ratio'), 0.0) >= 1.0
    oi_support = _safe_float(adv.get('oi_change_15m_pct', adv.get('oi_15m_pct', 0.0)), 0.0) >= 0.0 and _safe_float(adv.get('oi_change_1h_pct', adv.get('oi_1h_pct', 0.0)), 0.0) >= -0.15
    cvd_support = _safe_float(adv.get('cvd'), 0.0) > 0.0
    funding = _safe_float(adv.get('funding_rate_pct', adv.get('funding_rate', 0.0)), 0.0)
    funding_ok = funding < 0.03
    rsi = _safe_float(adv.get('rsi', 50.0), 50.0)
    not_overextended = rsi <= 78.0

    trigger = 'NONE'
    ready = False
    if direction == 'LONG' and oi_support and cvd_support and funding_ok and not_overextended:
        if reclaim_valid:
            trigger = 'BOS_RECLAIM'
            ready = True
        elif breakout_valid:
            trigger = 'BREAKOUT_RETEST'
            ready = True
        elif compression_release:
            trigger = 'COMPRESSION_RELEASE'
            ready = True

    logger.info(
        '[VIP_TRIGGER] %s | ready=%s | bias=%s | trigger=%s',
        symbol,
        1 if ready else 0,
        BIAS_BULLISH,
        trigger,
    )
    return {'vip_trigger_ready': ready, 'trigger_reason': trigger}


def build_vip_snapshot(
    *,
    symbol: str,
    adv: Dict[str, Any] | None = None,
    signal_payload: Dict[str, Any] | None = None,
    market_context: Dict[str, Any] | None = None,
    now: datetime | None = None,
) -> Dict[str, Any]:
    discovery = compute_accumulation_score(symbol, market_context=market_context, now=now)
    trigger = evaluate_vip_trigger_ready(
        symbol=symbol,
        vip_status=str(discovery['vip_status']),
        adv=adv,
        signal_payload=signal_payload,
        market_context=market_context,
    )
    if discovery['vip_status'] == VIP_CONFIRMED and trigger['vip_trigger_ready']:
        discovery['vip_status'] = VIP_TRIGGER_READY
        discovery['is_vip'] = True
    discovery['vip_trigger_ready'] = bool(trigger['vip_trigger_ready'])
    discovery['trigger_reason'] = str(trigger['trigger_reason'])
    return discovery


def get_vip_fast_lane_symbols(valid_symbols: Optional[List[str]] = None, price_map: Optional[Dict[str, float]] = None) -> Set[str]:
    """
    Getter publik untuk daftar simbol VIP fast lane.

    - Tanpa argumen: return snapshot cache terbaru untuk dipakai layer runtime/orchestration.
    - Dengan valid_symbols + price_map: hitung ulang cache dan return hasilnya.
    """
    global _VIP_FAST_LANE_CACHE
    if valid_symbols is None and price_map is None:
        return set(_VIP_FAST_LANE_CACHE)

    vip_list: Set[str] = set()
    valid_symbols = list(valid_symbols or [])
    price_map = dict(price_map or {})
    for sym in valid_symbols:
        sym_u = str(sym).upper()
        if _safe_float(price_map.get(sym_u, price_map.get(sym)), 0.0) <= 0.0:
            continue
        snapshot = compute_accumulation_score(sym_u)
        if snapshot['vip_status'] in {VIP_CONFIRMED, VIP_TRIGGER_READY}:
            vip_list.add(sym_u)

    _VIP_FAST_LANE_CACHE = set(vip_list)
    return set(vip_list)
def get_minutes_to_next_anchor() -> dict[str, int]:
    """Returns minutes remaining until the next session/candle anchor."""
    now = _utc_now()
    results = {}
    for name, spec in SESSION_SPECS.items():
        anchor = now.replace(hour=spec['hour'], minute=spec['minute'], second=0, microsecond=0)
        if anchor < now:
            anchor += timedelta(days=1)
        diff = (anchor - now).total_seconds() / 60
        results[name] = int(diff)
    return results
