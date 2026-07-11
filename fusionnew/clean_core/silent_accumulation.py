"""
silent_accumulation.py — Detect price compression + volume anomaly (non-blocking).
State machine: NO_ACCUMULATION → EARLY → VALID → READY.
Score: 0 (absent) / 3 (EARLY) / 7 (VALID) / 10 (READY).
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from collections import deque

import numpy as np

STATE_DIR = Path(os.getenv("STATE_DIR", "/app/runtime/state"))
STATE_DIR.mkdir(parents=True, exist_ok=True)
ACCUM_STATE_FILE = STATE_DIR / "accumulation_state.json"

# Thresholds
COMPRESSION_WINDOW = 20      # bars
COMPRESSION_PCT_MAX = 3.2    # max range % for compression
VOL_ANOMALY_WINDOW = 50      # bars for vol baseline
VOL_ANOMALY_MULT = 0.5       # vol < 50% of avg = anomaly
ABSORPTION_LOOKBACK = 10     # bars for absorption check
ABSORPTION_TICK_RATIO = 0.6  # min close>open ratio for bullish absorption

# State scores
STATE_SCORES = {
    "NO_ACCUMULATION": 0,
    "EARLY": 3,
    "VALID": 7,
    "READY": 10,
}


def _atomic_write(path: Path, data: dict):
    """Write JSON atomically: temp file → flush → rename."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w") as f:
        json.dump(data, f, default=str, indent=2)
    tmp.rename(path)


def _load_state() -> Dict[str, Any]:
    if ACCUM_STATE_FILE.exists():
        try:
            with open(ACCUM_STATE_FILE) as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def _save_state(state: Dict[str, Any]):
    _atomic_write(ACCUM_STATE_FILE, state)


def _rsi(close: np.ndarray, period: int = 14) -> float:
    if len(close) < period + 1:
        return 50.0
    delta = np.diff(close)
    gain = np.where(delta > 0, delta, 0.0)
    loss = np.where(delta < 0, -delta, 0.0)
    avg_gain = np.mean(gain[-period:])
    avg_loss = np.mean(loss[-period:])
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    rs = avg_gain / avg_loss
    return 100.0 - 100.0 / (1.0 + rs)


def _atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int = 14) -> float:
    if len(close) < period + 1:
        return 0.0
    prev_close = np.roll(close, 1)
    prev_close[0] = close[0]
    tr = np.maximum(high - low, np.maximum(np.abs(high - prev_close), np.abs(low - prev_close)))
    return float(np.mean(tr[-period:]))


def _detect_compression(high: np.ndarray, low: np.ndarray, close: np.ndarray, window: int = COMPRESSION_WINDOW) -> bool:
    """Price compression: range % < threshold over window."""
    if len(close) < window:
        return False
    h = high[-window:].max()
    l = low[-window:].min()
    if l == 0:
        return False
    range_pct = (h - l) / l * 100.0
    return range_pct < COMPRESSION_PCT_MAX


def _detect_volume_anomaly(volume: np.ndarray, window: int = VOL_ANOMALY_WINDOW, mult: float = VOL_ANOMALY_MULT) -> bool:
    """Volume anomaly: recent vol < mult * avg."""
    if len(volume) < window:
        return False
    avg = np.mean(volume[-window:-5]) if window > 5 else np.mean(volume[:-1])
    recent = np.mean(volume[-5:])
    return recent < avg * mult


def _detect_absorption(open_: np.ndarray, close: np.ndarray, high: np.ndarray, low: np.ndarray,
                        volume: np.ndarray, lookback: int = ABSORPTION_LOOKBACK) -> tuple[bool, str]:
    """
    Detect absorption: repeated tests of level with strong closes.
    Returns (is_absorption, bias) where bias is 'BULLISH' or 'BEARISH'.
    """
    if len(close) < lookback:
        return False, "NEUTRAL"
    
    # Bullish absorption: multiple tests of low with strong close > open
    low_level = low[-lookback:].min()
    tests_low = (low[-lookback:] <= low_level * 1.002).sum()
    strong_closes = (close[-lookback:] > open_[-lookback:]).sum()
    bull_ratio = strong_closes / lookback
    
    # Bearish absorption: multiple tests of high with strong close < open
    high_level = high[-lookback:].max()
    tests_high = (high[-lookback:] >= high_level * 0.998).sum()
    weak_closes = (close[-lookback:] < open_[-lookback:]).sum()
    bear_ratio = weak_closes / lookback
    
    if tests_low >= 3 and bull_ratio >= ABSORPTION_TICK_RATIO:
        return True, "BULLISH"
    if tests_high >= 3 and bear_ratio >= ABSORPTION_TICK_RATIO:
        return True, "BEARISH"
    
    return False, "NEUTRAL"


def _state_transition(current: str, compression: bool, vol_anomaly: bool, 
                       absorption: bool, absorption_bias: str, rsi: float) -> tuple[str, int]:
    """
    State machine logic.
    Returns (new_state, score).
    """
    if current == "NO_ACCUMULATION":
        if compression and vol_anomaly:
            return "EARLY", STATE_SCORES["EARLY"]
        return "NO_ACCUMULATION", STATE_SCORES["NO_ACCUMULATION"]
    
    if current == "EARLY":
        if absorption and absorption_bias != "NEUTRAL":
            return "VALID", STATE_SCORES["VALID"]
        if not compression or not vol_anomaly:
            return "NO_ACCUMULATION", STATE_SCORES["NO_ACCUMULATION"]
        return "EARLY", STATE_SCORES["EARLY"]
    
    if current == "VALID":
        if absorption and absorption_bias != "NEUTRAL" and rsi < 70:
            return "READY", STATE_SCORES["READY"]
        if not (compression and vol_anomaly):
            return "NO_ACCUMULATION", STATE_SCORES["NO_ACCUMULATION"]
        return "VALID", STATE_SCORES["VALID"]
    
    if current == "READY":
        if not (compression and vol_anomaly):
            return "NO_ACCUMULATION", STATE_SCORES["NO_ACCUMULATION"]
        return "READY", STATE_SCORES["READY"]
    
    return "NO_ACCUMULATION", STATE_SCORES["NO_ACCUMULATION"]


def detect_silent_accumulation(ltf_df, prev_state: str = "NO_ACCUMULATION") -> Dict[str, Any]:
    """
    Main entry point. Called by engine per candidate.
    
    Args:
        ltf_df: DataFrame with columns open, high, low, close, volume (at least 50 bars)
        prev_state: previous state for this symbol (optional)
    
    Returns:
        dict with keys: state, score, compression, vol_anomaly, absorption, absorption_bias, rsi
    """
    try:
        # Convert to numpy
        open_ = ltf_df["open"].to_numpy()
        high = ltf_df["high"].to_numpy()
        low = ltf_df["low"].to_numpy()
        close = ltf_df["close"].to_numpy()
        volume = ltf_df["volume"].to_numpy()
        
        if len(close) < COMPRESSION_WINDOW + 10:
            return {"state": "NO_ACCUMULATION", "score": 0, "reason": "insufficient_data"}
        
        # Indicators
        rsi = _rsi(close)
        compression = _detect_compression(high, low, close)
        vol_anomaly = _detect_volume_anomaly(volume)
        absorption, absorption_bias = _detect_absorption(open_, close, high, low, volume)
        
        # State transition
        state, score = _state_transition(
            prev_state, compression, vol_anomaly, absorption, absorption_bias, rsi
        )
        
        # Persist state
        state_data = _load_state()
        # Note: in production, key by symbol; here we just track one
        state_data["last_state"] = state
        _save_state(state_data)
        
        return {
            "state": state,
            "score": score,
            "compression": compression,
            "vol_anomaly": vol_anomaly,
            "absorption": absorption,
            "absorption_bias": absorption_bias,
            "rsi": round(rsi, 1),
        }
        
    except Exception as e:
        return {"state": "NO_ACCUMULATION", "score": 0, "reason": f"error: {e}"}