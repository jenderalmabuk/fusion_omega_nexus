"""
vip_fast_lane.py — Combine Silent + Whale + Session + SMC + Daily into VIP score.
Raw max = 117 → normalize to 0–100.
VIP_TRIGGER_READY = True if vip_score >= 85 (tunable).
Non-blocking: never bypasses ADVv2.
"""

import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional

# Raw score weights (sum = 117)
WHALE_WEIGHT = 36
SILENT_WEIGHT = 30
SESSION_WEIGHT = 18
SMC_WEIGHT = 15
DAILY_WEIGHT = 18

RAW_MAX = WHALE_WEIGHT + SILENT_WEIGHT + SESSION_WEIGHT + SMC_WEIGHT + DAILY_WEIGHT  # 117
TRIGGER_THRESHOLD = 85  # normalized score threshold for VIP_TRIGGER_READY


def _whale_score(whale_event: Optional[Dict[str, Any]]) -> float:
    """Score from whale event: 0–36."""
    if not whale_event:
        return 0.0
    
    bias = whale_event.get("bias", "NEUTRAL")
    confidence = whale_event.get("confidence_score", 0)
    value_usd = whale_event.get("value_usd", 0)
    age_weight = whale_event.get("age_weight", "NONE")
    
    # Base from bias + confidence
    if bias == "BULLISH":
        base = 1.0
    elif bias == "BEARISH":
        base = -1.0  # negative but we take abs for score
    else:
        base = 0.0
    
    # Confidence factor (0-1)
    conf_factor = confidence / 100.0
    
    # Value factor (cap at $10M)
    value_factor = min(value_usd / 10_000_000, 1.0)
    
    # Age weight factor
    age_factor = {
        "VERY_STRONG_UNCONFIRMED": 0.5,  # not confirmed yet
        "STRONG": 1.0,
        "RELEVANT": 0.7,
        "CONTEXT": 0.3,
        "NONE": 0.0,
    }.get(age_weight, 0.0)
    
    score = abs(base) * conf_factor * (0.4 + 0.3 * value_factor + 0.3 * age_factor)
    return min(score * WHALE_WEIGHT, WHALE_WEIGHT)


def _silent_score(accumulation: Dict[str, Any]) -> float:
    """Score from silent accumulation: 0–30."""
    state = accumulation.get("state", "NO_ACCUMULATION")
    score_map = {
        "READY": 1.0,
        "VALID": 0.7,
        "EARLY": 0.3,
        "NO_ACCUMULATION": 0.0,
    }
    return score_map.get(state, 0.0) * SILENT_WEIGHT


def _session_score(session: Dict[str, Any]) -> float:
    """Score from session timing: 0–18."""
    in_killzone = session.get("in_killzone", False)
    session_name = session.get("session", "OFF")
    
    if not in_killzone:
        return 0.0
    
    # London + NY overlap (12-14 UTC) = max
    if session_name == "LONDON":
        return 0.8 * SESSION_WEIGHT
    elif session_name == "NY":
        return 1.0 * SESSION_WEIGHT
    return 0.5 * SESSION_WEIGHT


def _smc_score(smc_context: Dict[str, Any]) -> float:
    """Score from SMC context: 0–15."""
    score = 0.0
    if smc_context.get("ob_detected"):
        score += 0.5
    if smc_context.get("imbalance"):
        score += 0.3
    if smc_context.get("breaker"):
        score += 0.2
    return min(score, 1.0) * SMC_WEIGHT


def _daily_score(daily_context: Dict[str, Any]) -> float:
    """Score from daily context: 0–18."""
    trend = daily_context.get("daily_trend", "NEUTRAL")
    structure = daily_context.get("daily_structure", "INTACT")
    
    score = 0.0
    if trend in ("BULLISH", "BEARISH"):
        score += 0.6
    if structure == "INTACT":
        score += 0.4
    return min(score, 1.0) * DAILY_WEIGHT


def _flow_score(flow_verdict: str) -> float:
    """Score from flow verdict: bonus/penalty on top of base."""
    if flow_verdict == "supportive":
        return 5.0  # small bonus
    elif flow_verdict == "hostile":
        return -10.0  # penalty
    return 0.0


def compute_vip_score(
    accumulation: Dict[str, Any],
    whale_event: Optional[Dict[str, Any]],
    session: Dict[str, Any],
    smc_context: Dict[str, Any],
    daily_context: Dict[str, Any],
    flow_verdict: str,
    setup_score: float = 0,
) -> Dict[str, Any]:
    """
    Main entry point.
    
    Returns:
        dict with keys:
        - vip_score (0-100 normalized)
        - raw_score (0-117)
        - trigger_ready (bool)
        - status (str)
        - components (dict breakdown)
    """
    # Component scores
    whale = _whale_score(whale_event)
    silent = _silent_score(accumulation)
    session_s = _session_score(session)
    smc = _smc_score(smc_context)
    daily = _daily_score(daily_context)
    flow = _flow_score(flow_verdict)
    
    raw_score = whale + silent + session_s + smc + daily + flow
    raw_score = max(0, min(raw_score, RAW_MAX))  # clamp
    
    # Normalize to 0-100
    vip_score = round(raw_score / RAW_MAX * 100, 1)
    
    # Trigger ready threshold
    trigger_ready = vip_score >= TRIGGER_THRESHOLD
    
    # Status label
    if vip_score >= 90:
        status = "VIP_EXCEPTIONAL"
    elif vip_score >= 85:
        status = "VIP_TRIGGER_READY"
    elif vip_score >= 70:
        status = "VIP_STRONG"
    elif vip_score >= 50:
        status = "VIP_MODERATE"
    elif vip_score >= 30:
        status = "VIP_WEAK"
    else:
        status = "VIP_LOW"
    
    return {
        "vip_score": vip_score,
        "raw_score": round(raw_score, 1),
        "trigger_ready": trigger_ready,
        "status": status,
        "components": {
            "whale": round(whale, 1),
            "silent": round(silent, 1),
            "session": round(session_s, 1),
            "smc": round(smc, 1),
            "daily": round(daily, 1),
            "flow": round(flow, 1),
        },
    }