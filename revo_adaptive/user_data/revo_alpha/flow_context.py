#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""REVO FLOW CONTEXT LAYER v1.3.7

Canonical contract policy:
1. For published pairs, read revo_execution_context.json first.
2. Every published pair must have an explicit execution context row.
3. UNKNOWN is not a normal flow state. Missing contract becomes NO_TRADE with DENY_CONTEXT_CONTRACT_BROKEN.
4. Fallback to revo_flow_context.json is only for backward compatibility.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

import numpy as np
import pandas as pd
from pandas import DataFrame

# REVO PATCH: datetime-safe merge_asof helper.
_REVO_ORIG_MERGE_ASOF = pd.merge_asof

def _revo_safe_merge_asof(left, right, *args, **kwargs):
    left_on = kwargs.get("left_on") or kwargs.get("on")
    right_on = kwargs.get("right_on") or kwargs.get("on")
    try:
        if left_on and right_on and left_on in left.columns and right_on in right.columns:
            left2 = left.copy(); right2 = right.copy()
            lkey = "__revo_left_ts_ns"; rkey = "__revo_right_ts_ns"
            left2[lkey] = pd.to_datetime(left2[left_on], utc=True, errors="coerce").astype("int64")
            right2[rkey] = pd.to_datetime(right2[right_on], utc=True, errors="coerce").astype("int64")
            left2 = left2[left2[lkey] > 0].sort_values(lkey)
            right2 = right2[right2[rkey] > 0].sort_values(rkey)
            kwargs = dict(kwargs)
            if "on" in kwargs:
                kwargs.pop("on", None)
            kwargs["left_on"] = lkey; kwargs["right_on"] = rkey
            out = _REVO_ORIG_MERGE_ASOF(left2, right2, *args, **kwargs)
            for c in [lkey, rkey]:
                if c in out.columns:
                    out = out.drop(columns=[c])
            return out
    except Exception:
        pass
    return _REVO_ORIG_MERGE_ASOF(left, right, *args, **kwargs)

# CONTROL_TOWER_F2B_RUNTIME_DIR_RESOLVER_START
# Purpose: dual exchange runtime contract separation.
# - REVO_RUNTIME_DIR has highest priority and can point to /freqtrade/user_data/revo_alpha/runtime/bybit.
# - REVO_RUNTIME_PROFILE=binance/bybit maps to user_data/revo_alpha/runtime/<profile>.
# - Default remains the legacy root runtime to avoid breaking the active Binance stack.
def _ct_f2b_resolve_runtime_dir() -> Path:
    explicit = str(os.environ.get("REVO_RUNTIME_DIR", "")).strip()
    if explicit:
        return Path(explicit)
    profile = str(os.environ.get("REVO_RUNTIME_PROFILE", "")).strip().lower()
    if profile in {"binance", "bybit"}:
        return Path("user_data/revo_alpha/runtime") / profile
    return Path("user_data/revo_alpha/runtime")

RUNTIME_DIR = _ct_f2b_resolve_runtime_dir()
# CONTROL_TOWER_F2B_RUNTIME_DIR_RESOLVER_END
FLOW_CONTEXT_CSV = RUNTIME_DIR / "revo_flow_context.csv"
FLOW_CONTEXT_JSON = RUNTIME_DIR / "revo_flow_context.json"
STICKY_STATE_JSON = RUNTIME_DIR / "pair_universe_sticky_state.json"
REMOTE_JSON = RUNTIME_DIR / "pair_universe_remote.json"
EXECUTION_CONTEXT_JSON = RUNTIME_DIR / "revo_execution_context.json"

DEFAULT_FLOW = {
    "price_delta_pct": 0.0,
    "price_delta_pct_15m": 0.0,
    "price_delta_pct_1h": 0.0,
    "oi_delta_pct": 0.0,
    "oi_delta_pct_15m": 0.0,
    "oi_delta_pct_1h": 0.0,
    "cvd_delta": 0.0,
    "cvd_delta_15m": 0.0,
    "cvd_zscore": 0.0,
    "cvd_zscore_15m": 0.0,
    "funding_rate": 0.0,
    "funding_zscore": 0.0,
    "volume_zscore": 0.0,
    "volume_zscore_15m": 0.0,
    "flow_quadrant": "NO_FLOW",
    "flow_direction": "NO_TRADE",
    "flow_strength": "NO_FLOW",
    "flow_authority": "NO_TRADE",
    "flow_risk": "NORMAL",
    "flow_ready": False,
    "data_ready": False,
    "data_quality": "NO_FLOW",
    "flow_lookup_source": "DEFAULT",
    "context_contract_status": "NO_CONTEXT",
    "cycle_id": "NONE",
    "publish_reason": "NONE",
    "entry_permission": "NO_TRADE",
    "deny_reason": "DENY_FLOW_DATA_NOT_READY",
    "scanner_mode": "UNKNOWN_SCANNER",
    "btc_mode": "BTC_UNKNOWN",
    "btc_weight": 0.0,
    "btc_weight_label": "UNKNOWN",
    "coupling_status": "UNKNOWN_COUPLING",
    "btc_alignment": "UNKNOWN",
    "sticky_status": "NONE",
    "sticky_current_direction": "NO_TRADE",
    "sticky_last_direction": "NO_TRADE",
    "sticky_current_quadrant": "NO_FLOW",
    "sticky_last_quadrant": "NO_FLOW",
    "sticky_current_strength": "NO_FLOW",
    "sticky_last_strength": "NO_FLOW",
    "sticky_age_sec": 0.0,
    "sticky_expires_in_sec": 0.0,
}


def _runtime_path(path: Path) -> Path:
    if path.exists():
        return path
    return Path("/freqtrade") / path


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        out = float(value)
        if np.isnan(out) or np.isinf(out):
            return default
        return out
    except Exception:
        return default


def _load_json(path: Path, default: Any) -> Any:
    path = _runtime_path(path)
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return default


def _load_sticky_state_row(pair: str) -> Dict[str, Any]:
    data = _load_json(STICKY_STATE_JSON, {})
    pairs = data.get("pairs", {}) if isinstance(data, dict) else {}
    row = pairs.get(pair, {}) if isinstance(pairs, dict) else {}
    return row if isinstance(row, dict) else {}


def _attach_sticky_metadata(row: Dict[str, Any], pair: str, source: str) -> Dict[str, Any]:
    sticky = _load_sticky_state_row(pair)
    out = dict(DEFAULT_FLOW)
    out.update(row or {})
    out["flow_lookup_source"] = source
    out["sticky_status"] = str(out.get("sticky_status") or sticky.get("status", "NONE")) if sticky else str(out.get("sticky_status", "NONE"))
    out["sticky_current_direction"] = str(sticky.get("current_direction", out.get("current_direction", "NO_TRADE"))) if sticky else str(out.get("current_direction", "NO_TRADE"))
    out["sticky_last_direction"] = str(sticky.get("last_direction", out.get("last_direction", "NO_TRADE"))) if sticky else str(out.get("last_direction", "NO_TRADE"))
    out["sticky_current_quadrant"] = str(sticky.get("current_quadrant", out.get("current_quadrant", "NO_FLOW"))) if sticky else str(out.get("current_quadrant", "NO_FLOW"))
    out["sticky_last_quadrant"] = str(sticky.get("last_quadrant", out.get("last_quadrant", "NO_FLOW"))) if sticky else str(out.get("last_quadrant", "NO_FLOW"))
    out["sticky_current_strength"] = str(sticky.get("current_strength", out.get("current_strength", "NO_FLOW"))) if sticky else str(out.get("current_strength", "NO_FLOW"))
    out["sticky_last_strength"] = str(sticky.get("last_strength", out.get("last_strength", "NO_FLOW"))) if sticky else str(out.get("last_strength", "NO_FLOW"))
    out["sticky_age_sec"] = _safe_float(sticky.get("sticky_age_sec"), _safe_float(out.get("sticky_age_sec"), 0.0)) if sticky else _safe_float(out.get("sticky_age_sec"), 0.0)
    out["sticky_expires_in_sec"] = _safe_float(sticky.get("sticky_expires_in_sec"), _safe_float(out.get("sticky_expires_in_sec"), 0.0)) if sticky else _safe_float(out.get("sticky_expires_in_sec"), 0.0)
    return _normalize_row(out)


def _normalize_row(row: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(DEFAULT_FLOW)
    out.update(row or {})
    # Normal path must not emit UNKNOWN for flow direction/quadrant/strength.
    for key, default in {
        "flow_direction": "NO_TRADE",
        "flow_quadrant": "NO_FLOW",
        "flow_strength": "NO_FLOW",
        "flow_authority": "NO_TRADE",
        "flow_risk": "NORMAL",
    }.items():
        val = str(out.get(key, default) or default).upper()
        if val == "UNKNOWN":
            val = default
        out[key] = val
    out["data_ready"] = bool(out.get("data_ready", out.get("flow_ready", False)))
    out["flow_ready"] = bool(out.get("flow_ready", out.get("data_ready", False)))
    # Legacy aliases expected by older code.
    out["price_delta_pct"] = _safe_float(out.get("price_delta_pct", out.get("price_delta_pct_15m", 0.0)), 0.0)
    out["oi_delta_pct"] = _safe_float(out.get("oi_delta_pct", out.get("oi_delta_pct_15m", 0.0)), 0.0)
    out["cvd_zscore"] = _safe_float(out.get("cvd_zscore", out.get("cvd_zscore_15m", 0.0)), 0.0)
    out["cvd_delta"] = _safe_float(out.get("cvd_delta", out.get("cvd_delta_15m", 0.0)), 0.0)
    out["volume_zscore"] = _safe_float(out.get("volume_zscore", out.get("volume_zscore_15m", 0.0)), 0.0)
    return out


def _is_pair_published(pair: str) -> bool:
    data = _load_json(REMOTE_JSON, {})
    return pair in set(str(p) for p in (data.get("pairs", []) if isinstance(data, dict) else []))


def _load_execution_context(pair: str) -> Optional[Dict[str, Any]]:
    data = _load_json(EXECUTION_CONTEXT_JSON, None)
    if not isinstance(data, dict):
        return None
    pairs = data.get("pairs", {}) if isinstance(data.get("pairs", {}), dict) else {}
    if pair in pairs and isinstance(pairs[pair], dict):
        row = dict(pairs[pair])
        row["flow_lookup_source"] = "EXECUTION_CONTEXT"
        row["context_contract_status"] = str(data.get("contract_status", "OK"))
        row["cycle_id"] = str(data.get("cycle_id", row.get("cycle_id", "NONE")))
        return _attach_sticky_metadata(row, pair, "EXECUTION_CONTEXT")
    if _is_pair_published(pair):
        row = dict(DEFAULT_FLOW)
        row.update({
            "data_quality": "REMOTE_PAIR_MISSING_EXECUTION_CONTEXT",
            "context_contract_status": "BROKEN",
            "deny_reason": "DENY_CONTEXT_CONTRACT_BROKEN",
            "flow_lookup_source": "EXECUTION_CONTEXT_MISSING_PAIR",
            "flow_direction": "NO_TRADE",
            "flow_quadrant": "NO_FLOW",
            "flow_strength": "NO_FLOW",
            "data_ready": False,
            "flow_ready": False,
        })
        return _attach_sticky_metadata(row, pair, "EXECUTION_CONTEXT_MISSING_PAIR")
    return None


def _load_latest_json(pair: str) -> Dict[str, Any]:
    ctx = _load_execution_context(pair)
    if ctx is not None:
        return ctx
    path = _runtime_path(FLOW_CONTEXT_JSON)
    if not path.exists():
        row = dict(DEFAULT_FLOW)
        row["data_quality"] = "MISSING_FLOW_CONTEXT_JSON"
        return _attach_sticky_metadata(row, pair, "MISSING_JSON")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        row = dict(DEFAULT_FLOW)
        row["data_quality"] = "BROKEN_FLOW_CONTEXT_JSON"
        return _attach_sticky_metadata(row, pair, "BROKEN_JSON")
    if isinstance(data, dict):
        pair_like_keys = [k for k in data.keys() if "/USDT" in str(k) or str(k).endswith(":USDT")]
        if pair in data and isinstance(data[pair], dict):
            row = dict(data[pair])
            row["flow_lookup_source"] = "JSON_CURRENT_FLOW_FALLBACK"
            return _attach_sticky_metadata(row, pair, "JSON_CURRENT_FLOW_FALLBACK")
        if pair_like_keys:
            sticky = _load_sticky_state_row(pair)
            row = dict(DEFAULT_FLOW)
            if sticky:
                row.update({
                    "flow_quadrant": sticky.get("current_quadrant", "NO_FLOW"),
                    "flow_direction": sticky.get("current_direction", "NO_TRADE"),
                    "flow_strength": sticky.get("current_strength", "NO_FLOW"),
                    "data_quality": "STICKY_STATE_FALLBACK_NO_EXECUTION_CONTEXT",
                    "deny_reason": "DENY_CONTEXT_CONTRACT_BROKEN",
                })
            else:
                row["data_quality"] = "MISSING_PAIR_FLOW_CONTEXT"
            return _attach_sticky_metadata(row, pair, "STICKY_STATE_FALLBACK" if sticky else "JSON_PAIR_MISSING")
        row = dict(data)
        row["flow_lookup_source"] = "JSON_LEGACY_GLOBAL"
        return _attach_sticky_metadata(row, pair, "JSON_LEGACY_GLOBAL")
    return _attach_sticky_metadata(dict(DEFAULT_FLOW), pair, "JSON_NOT_DICT")


def _load_flow_csv(pair: str) -> Optional[DataFrame]:
    path = _runtime_path(FLOW_CONTEXT_CSV)
    if not path.exists():
        return None
    try:
        ctx = pd.read_csv(path)
    except Exception:
        return None
    if ctx.empty:
        return None
    if "pair" in ctx.columns:
        ctx = ctx[ctx["pair"].astype(str) == str(pair)].copy()
    if ctx.empty or "date" not in ctx.columns:
        return None
    ctx["date"] = pd.to_datetime(ctx["date"], utc=True, errors="coerce")
    ctx = ctx.dropna(subset=["date"]).sort_values("date")
    return ctx if not ctx.empty else None


def _derive_quadrant(price_delta: pd.Series, oi_delta: pd.Series, cvd_z: pd.Series, funding_z: pd.Series) -> pd.Series:
    label = pd.Series("NO_FLOW", index=price_delta.index, dtype="object")
    price_up = price_delta > 0
    price_down = price_delta < 0
    oi_up = oi_delta > 0
    oi_down = oi_delta < 0
    cvd_up = cvd_z > 0.25
    cvd_down = cvd_z < -0.25
    funding_neg = funding_z < -0.25
    funding_pos = funding_z > 0.25
    label[price_up & oi_up & cvd_up & ~funding_pos] = "BULLISH_CONTINUATION_FRESH"
    label[price_down & oi_up & cvd_down & ~funding_neg] = "BEARISH_CONTINUATION_FRESH"
    label[price_up & oi_up & (~cvd_up | funding_pos)] = "BULL_TRAP_RISK"
    label[price_down & oi_up & (~cvd_down | funding_neg)] = "BEAR_TRAP_RISK"
    label[price_up & oi_down & cvd_up] = "SHORT_COVERING_NORMAL"
    label[price_down & oi_down & cvd_down] = "LONG_UNWIND_NORMAL"
    return label


def add_revo_flow_context_features(dataframe: DataFrame, pair: str = "", metadata: Optional[Dict[str, Any]] = None) -> DataFrame:
    if dataframe is None or dataframe.empty:
        return dataframe
    df = dataframe.copy()
    if metadata and not pair:
        pair = str(metadata.get("pair", ""))
    latest = _load_latest_json(pair)
    use_latest = not str(latest.get("flow_lookup_source", "")).startswith("CSV")
    ctx = None if use_latest else _load_flow_csv(pair)
    if ctx is not None and "date" in df.columns:
        work = df.copy()
        work["date"] = pd.to_datetime(work["date"], utc=True, errors="coerce")
        work = work.sort_values("date")
        keep_cols = ["date", "price_delta_pct", "oi_delta_pct", "cvd_delta", "cvd_zscore", "funding_rate", "funding_zscore", "volume_zscore", "flow_quadrant"]
        keep_cols = [c for c in keep_cols if c in ctx.columns]
        ctx = ctx[keep_cols].copy()
        merged = _revo_safe_merge_asof(work, ctx, on="date", direction="backward").sort_index()
        for c in keep_cols:
            if c != "date":
                df[f"revo_flow_{c}"] = merged[c].values
        df["revo_flow_data_ready"] = df.get("revo_flow_price_delta_pct", pd.Series(index=df.index)).notna().astype(int)
    else:
        for key, value in latest.items():
            if key in {"data_ready"}:
                continue
            col = f"revo_flow_{key}"
            if isinstance(value, (int, float, bool, np.integer, np.floating, np.bool_)):
                df[col] = pd.Series(value, index=df.index, dtype="float64")
            else:
                df[col] = str(value)
        df["revo_flow_data_ready"] = int(bool(latest.get("data_ready", False)))

    numeric_defaults = {
        "revo_flow_price_delta_pct": 0.0,
        "revo_flow_price_delta_pct_15m": 0.0,
        "revo_flow_price_delta_pct_1h": 0.0,
        "revo_flow_oi_delta_pct": 0.0,
        "revo_flow_oi_delta_pct_15m": 0.0,
        "revo_flow_oi_delta_pct_1h": 0.0,
        "revo_flow_cvd_delta": 0.0,
        "revo_flow_cvd_delta_15m": 0.0,
        "revo_flow_cvd_zscore": 0.0,
        "revo_flow_cvd_zscore_15m": 0.0,
        "revo_flow_funding_rate": 0.0,
        "revo_flow_funding_zscore": 0.0,
        "revo_flow_volume_zscore": 0.0,
        "revo_flow_volume_zscore_15m": 0.0,
        "revo_flow_btc_weight": 0.0,
    }
    for col, default in numeric_defaults.items():
        if col not in df.columns:
            df[col] = default
        df[col] = pd.to_numeric(df[col], errors="coerce").replace([np.inf, -np.inf], np.nan).fillna(default)
    if "revo_flow_flow_quadrant" not in df.columns:
        df["revo_flow_flow_quadrant"] = _derive_quadrant(df["revo_flow_price_delta_pct"], df["revo_flow_oi_delta_pct"], df["revo_flow_cvd_zscore"], df["revo_flow_funding_zscore"])
    else:
        df["revo_flow_flow_quadrant"] = df["revo_flow_flow_quadrant"].fillna("NO_FLOW").astype(str).str.upper().replace("UNKNOWN", "NO_FLOW")
    if "revo_flow_flow_direction" not in df.columns:
        df["revo_flow_flow_direction"] = "NO_TRADE"
    else:
        df["revo_flow_flow_direction"] = df["revo_flow_flow_direction"].fillna("NO_TRADE").astype(str).str.upper().replace("UNKNOWN", "NO_TRADE")
    # Track previous candle direction for flip detection
    df["revo_flow_prev_direction"] = df["revo_flow_flow_direction"].shift(1).fillna("NO_TRADE")
    if "revo_flow_flow_strength" not in df.columns:
        df["revo_flow_flow_strength"] = "NO_FLOW"
    else:
        df["revo_flow_flow_strength"] = df["revo_flow_flow_strength"].fillna("NO_FLOW").astype(str).str.upper().replace("UNKNOWN", "NO_FLOW")

    q = df["revo_flow_flow_quadrant"].astype(str).str.upper()
    df["revo_flow_long_bias_score"] = 0.0
    df["revo_flow_short_bias_score"] = 0.0
    df["revo_flow_trap_risk_score"] = 0.0
    df["revo_flow_squeeze_risk_score"] = 0.0
    bull = q.str.contains("BULLISH_CONTINUATION|SHORT_COVERING", regex=True)
    bear = q.str.contains("BEARISH_CONTINUATION|LONG_UNWIND", regex=True)
    trap = q.str.contains("TRAP_RISK|BULL_TRAP|BEAR_TRAP", regex=True)
    squeeze = q.str.contains("COVERING|UNWIND|SQUEEZE", regex=True)
    df.loc[bull, "revo_flow_long_bias_score"] = 65.0
    df.loc[bear, "revo_flow_short_bias_score"] = 65.0
    df.loc[trap, "revo_flow_trap_risk_score"] = 65.0
    df.loc[squeeze, "revo_flow_squeeze_risk_score"] = 50.0
    df["revo_flow_direction_code"] = 0
    explicit = df["revo_flow_flow_direction"].astype(str).str.upper()
    df.loc[explicit == "LONG_ONLY", "revo_flow_direction_code"] = 1
    df.loc[explicit == "SHORT_ONLY", "revo_flow_direction_code"] = -1
    return df
