from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
from pandas import DataFrame

from freqtrade.strategy import IStrategy
from freqtrade.persistence import Trade

logger = logging.getLogger(__name__)


class RevoAdaptiveStrategy(IStrategy):
    """Revo Adaptive v1 smoke-test strategy.

    Purpose: initial executable baseline based on the rebuild blueprint.
    This version is self-contained for backtesting and uses price/volume proxies.
    Real flow/regime JSON integration comes in Phase 2.
    """

    INTERFACE_VERSION = 3
    timeframe = "5m"
    can_short = False

    minimal_roi = {"0": 0.08, "180": 0.04, "360": 0.02, "720": 0}
    stoploss = -0.02

    trailing_stop = False
    trailing_stop_positive = 0.50
    use_exit_signal = False
    use_custom_stoploss = False
    position_adjustment_enable = True
    startup_candle_count = 240

    process_only_new_candles = True
    _flow_cache = None
    _flow_cache_mtime = None

    def _load_flow_context(self) -> dict:
        path = os.environ.get(
            "REVO_FLOW_CONTEXT_PATH",
            "/freqtrade/user_data/revo_alpha/runtime/bybit/revo_flow_context.json",
        )
        p = Path(path)
        try:
            mtime = p.stat().st_mtime
            if self._flow_cache is None or self._flow_cache_mtime != mtime:
                self._flow_cache = json.loads(p.read_text())
                self._flow_cache_mtime = mtime
        except Exception:
            self._flow_cache = {}
            self._flow_cache_mtime = None
        return self._flow_cache or {}

    @staticmethod
    def _flow_is_stale(rec: dict, max_age_sec: int = 120) -> bool:
        ts = rec.get("ts") or rec.get("timestamp")
        if not ts:
            return True
        try:
            t = datetime.fromisoformat(str(ts).replace("Z", "+00:00")).astimezone(timezone.utc)
            return (datetime.now(timezone.utc) - t).total_seconds() > max_age_sec
        except Exception:
            return True

    def _flow_record(self, pair: str) -> dict:
        ctx = self._load_flow_context()
        records = ctx.get("pairs", ctx) if isinstance(ctx, dict) else {}
        return records.get(pair, {}) if isinstance(records, dict) else {}

    def _cfg(self):
        return {
            "min_score": int(float(os.environ.get("REVO_ENTRY_MIN_SCORE", os.environ.get("SIG_ENTRY_MIN_SCORE", "7")))),
            "discount": float(os.environ.get("REVO_ENTRY_DISCOUNT_MIN_PCT", os.environ.get("SIG_ENTRY_DISCOUNT_MIN_PCT", "2.5"))),
            # Falling-knife floor: reject entries deeper than this % below EMA55.
            # Default 999 = disabled (backward compatible). Backtest (Jun1-Jul10,
            # 93 pairs) shows dist_ema55 < -7% flips net-negative (win% <20%);
            # a -7% floor drops 150 losers (net -57.8) and lifts net +114.9 -> +172.7.
            "discount_max": float(os.environ.get("REVO_ENTRY_DISCOUNT_MAX_PCT", "999")),
            "rsi_max": float(os.environ.get("REVO_ENTRY_RSI_MAX", os.environ.get("SIG_ENTRY_RSI_MAX", "45"))),
            "min_qvol": float(os.environ.get("REVO_MIN_QVOL_5M", os.environ.get("SIG_MIN_QVOL_5M", "200000"))),
            "er_chop": float(os.environ.get("REVO_ER_CHOP_MAX", "0.15")),
            "atr_max": float(os.environ.get("REVO_ATR_PCT_MAX", "4.0")),
            # liq_mode: "instant" (current candle qvol) or "med48" (rolling 48-candle median)
            "liq_mode": os.environ.get("REVO_LIQ_MODE", "instant"),
        }

    @staticmethod
    def _rsi(close: pd.Series, period: int = 14) -> pd.Series:
        delta = close.diff()
        gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
        loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
        rs = gain / loss.replace(0, np.nan)
        return (100 - (100 / (1 + rs))).fillna(50)

    @staticmethod
    def _atr(df: DataFrame, period: int = 14) -> pd.Series:
        tr = pd.concat([
            (df["high"] - df["low"]),
            (df["high"] - df["close"].shift()).abs(),
            (df["low"] - df["close"].shift()).abs(),
        ], axis=1).max(axis=1)
        return tr.ewm(alpha=1 / period, adjust=False).mean()

    @staticmethod
    def _er(close: pd.Series, window: int = 48) -> pd.Series:
        direction = (close - close.shift(window)).abs()
        volatility = close.diff().abs().rolling(window).sum().replace(0, np.nan)
        return (direction / volatility).clip(0, 1).fillna(0)

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        c = self._cfg()
        df = dataframe
        close = df["close"]
        volume = df["volume"]

        df["ema55"] = close.ewm(span=55, adjust=False).mean()
        df["ema50"] = close.ewm(span=50, adjust=False).mean()
        df["ema200"] = close.ewm(span=200, adjust=False).mean()
        df["rsi"] = self._rsi(close, 14)
        df["atr"] = self._atr(df, 14)
        df["atr_pct"] = (df["atr"] / close * 100).fillna(0)
        df["er48"] = self._er(close, 48)

        df["qvol_5m"] = (close * volume).fillna(0)
        # Rolling median qvol over 48 candles (4h) — checks PAIR liquidity, not candle
        df["qvol_5m_med48"] = df["qvol_5m"].rolling(48, min_periods=12).median().fillna(0)
        vol_ma = volume.rolling(48).mean().replace(0, np.nan)
        df["vol_z_proxy"] = (volume / vol_ma).replace([np.inf, -np.inf], np.nan).fillna(1.0)
        df["cvd_proxy"] = ((close - close.shift(3)) / close.shift(3).replace(0, np.nan) * df["vol_z_proxy"]).fillna(0)
        df["oi_proxy"] = volume.pct_change(3).replace([np.inf, -np.inf], np.nan).fillna(0) * 100

        flow = self._flow_record(metadata.get("pair", ""))
        use_real_flow = bool(flow) and not self._flow_is_stale(flow, int(os.environ.get("REVO_FLOW_MAX_AGE_SEC", "660")))
        flow_direction = str(flow.get("flow_direction") or flow.get("flow_authority") or "NO_TRADE").upper()
        real_cvd_z = float(flow.get("cvd_zscore_15m", flow.get("cvd_zscore", flow.get("cvd_z", 0))) or 0)
        real_oi = float(flow.get("oi_delta_pct_15m", flow.get("oi_delta_15m_pct", flow.get("oi15", 0))) or 0)
        real_funding_z = float(flow.get("funding_zscore", 0) or 0)
        real_funding_rate = float(flow.get("funding_rate", 0) or 0)
        real_vol_z = float(flow.get("volume_zscore_15m", flow.get("volume_zscore", flow.get("volume_z", 0))) or 0)
        df["real_flow_available"] = int(use_real_flow)
        df["real_flow_long"] = int(use_real_flow and flow_direction in ("LONG_ONLY", "BOTH_ALLOWED"))
        df["real_flow_hostile"] = int(use_real_flow and flow_direction in ("SHORT_ONLY", "NO_TRADE"))
        df["real_cvd_z"] = real_cvd_z
        df["real_oi_delta"] = real_oi
        df["real_funding_z"] = real_funding_z
        df["real_funding_rate"] = real_funding_rate
        df["real_vol_z"] = real_vol_z

        df["dist_ema55_pct"] = ((close / df["ema55"] - 1.0) * 100).fillna(0)
        df["at_discount"] = (df["dist_ema55_pct"] <= -c["discount"]).astype(int)
        # Falling-knife guard: 1 when price is NOT dislocated deeper than discount_max
        # below EMA55. Deep dislocation = crash, not a healthy pullback (see _cfg note).
        df["not_falling_knife"] = (df["dist_ema55_pct"] >= -c["discount_max"]).astype(int)
        df["rsi_ok"] = (df["rsi"] <= c["rsi_max"]).astype(int)
        df["liq_ok"] = (df["qvol_5m_med48"] >= c["min_qvol"]).astype(int) if c.get("liq_mode") == "med48" else (df["qvol_5m"] >= c["min_qvol"]).astype(int)
        df["vol_ok"] = np.where(df["real_flow_available"] == 1, (df["real_vol_z"] >= -0.5).astype(int), (df["vol_z_proxy"] >= 0.8).astype(int))

        # FIX: cvd_ok threshold relaxed from >0 to >-0.5 (don't require net buying, just not aggressive selling)
        df["cvd_ok"] = np.where(df["real_flow_available"] == 1, (df["real_cvd_z"] > -0.5).astype(int), (df["cvd_proxy"] > -0.5).astype(int))

        # FIX: oi_ok neutral when data missing/zero (was penalizing score)
        df["oi_ok"] = np.where(df["real_flow_available"] == 1, (df["real_oi_delta"].abs() > 0).astype(int), 1)

        # Real funding when context is available; neutral fallback in pure OHLCV backtests.
        df["funding_ok"] = np.where(df["real_flow_available"] == 1, ((df["real_funding_z"] <= -1.0) | (df["real_funding_rate"] <= 0)).astype(int), 1)
        df["funding_crowded"] = np.where(df["real_flow_available"] == 1, ((df["real_funding_z"] >= 1.0) | (df["real_funding_rate"] >= 0.0003)).astype(int), 0)

        df["pair_uptrend_pullback"] = ((df["ema50"] > df["ema200"]) & (df["at_discount"] == 1)).astype(int)
        df["er_chop"] = (df["er48"] < c["er_chop"]).astype(int)
        df["atr_explosive"] = (df["atr_pct"] > c["atr_max"]).astype(int)
        df["btc_ok"] = 1
        df["btc_dump"] = 0

        df["entry_score"] = (
            df["at_discount"] * 2 +
            df["rsi_ok"] +
            df["cvd_ok"] * 2 +
            df["oi_ok"] +
            df["funding_ok"] * 2 +
            df["pair_uptrend_pullback"] +
            df["btc_ok"] +
            df["vol_ok"] -
            df["er_chop"] -
            df["btc_dump"] -
            df["atr_explosive"] -
            df["funding_crowded"] * 2
        ).astype(int)

        return df

    _entry_audit_seen = set()

    def _audit_entry_signal(self, dataframe: DataFrame, metadata: dict, cond) -> None:
        if dataframe.empty or not bool(cond.iloc[-1]):
            return
        row = dataframe.iloc[-1]
        pair = metadata.get("pair", "")
        candle_time = str(row.get("date", row.name))
        key = f"{pair}|{candle_time}"
        if key in self._entry_audit_seen:
            return
        self._entry_audit_seen.add(key)
        payload = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "pair": pair,
            "candle_time": candle_time,
            "entry_score": float(row.get("entry_score", 0)),
            "real_flow_available": int(row.get("real_flow_available", 0)),
            "real_flow_hostile": int(row.get("real_flow_hostile", 0)),
            "real_flow_long": int(row.get("real_flow_long", 0)),
            "real_cvd_z": float(row.get("real_cvd_z", 0)),
            "real_oi_delta": float(row.get("real_oi_delta", 0)),
            "real_funding_z": float(row.get("real_funding_z", 0)),
            "real_funding_rate": float(row.get("real_funding_rate", 0)),
            "real_vol_z": float(row.get("real_vol_z", 0)),
            "cvd_proxy": float(row.get("cvd_proxy", 0)),
            "oi_proxy": float(row.get("oi_proxy", 0)),
            "vol_z_proxy": float(row.get("vol_z_proxy", 0)),
            "at_discount": int(row.get("at_discount", 0)),
            "rsi": float(row.get("rsi", 0)),
            "dist_ema55_pct": float(row.get("dist_ema55_pct", 0)),
            "liq_ok": int(row.get("liq_ok", 0)),
            "er_chop": int(row.get("er_chop", 0)),
            "atr_explosive": int(row.get("atr_explosive", 0)),
            "funding_ok": int(row.get("funding_ok", 0)),
            "funding_crowded": int(row.get("funding_crowded", 0)),
        }
        path = Path(os.environ.get("REVO_ENTRY_AUDIT_PATH", "/freqtrade/user_data/local/revo_entry_audit.jsonl"))
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a") as f:
                f.write(json.dumps(payload, sort_keys=True) + "\n")
        except Exception:
            pass

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        c = self._cfg()
        dataframe["enter_long"] = 0

        # Pullback identity: require uptrend context (additive bonus in score)
        # and flow alignment when real data available
        flow_guard = (
            (dataframe["real_flow_available"] == 0) |  # no flow data = allow
            (dataframe["real_flow_long"] == 1)         # flow allows LONG
        )
        # Quality gates: liquidity + score + RSI + not explosive ATR + flow
        # Discount & trend are ADDITIVE in score, not hard requirements
        cond = (
            (dataframe["liq_ok"] == 1) &
            (dataframe["entry_score"] >= c["min_score"]) &
            (dataframe["rsi_ok"] == 1) &
            (dataframe["atr_explosive"] == 0) &
            (dataframe["not_falling_knife"] == 1) &
            flow_guard
        )

        # --- NEAR-MISS LOGGING ---
        _near = dataframe.iloc[-1]
        _score = int(_near.get("entry_score", 0))
        _discount = int(_near.get("at_discount", 0))
        _rsi = int(_near.get("rsi_ok", 0))
        _cvd = int(_near.get("cvd_ok", 0))
        _oi = int(_near.get("oi_ok", 0))
        _fund = int(_near.get("funding_ok", 0))
        _liq = int(_near.get("liq_ok", 0))
        _chop = int(_near.get("er_chop", 0))
        _atr = int(_near.get("atr_explosive", 0))
        _crowd = int(_near.get("funding_crowded", 0))
        _trend = int(_near.get("pair_uptrend_pullback", 0))
        _candle = str(_near.get("date", ""))
        if _score >= c["min_score"] - 2 and not bool(cond.iloc[-1]):
            _fail = []
            if not _discount: _fail.append("discount")
            if not _rsi: _fail.append("rsi")
            if not _cvd: _fail.append("cvd")
            if not _oi: _fail.append("oi")
            if not _fund: _fail.append("funding")
            if not _liq: _fail.append("liq")
            if _chop: _fail.append("chop")
            if _atr: _fail.append("atr")
            if _crowd: _fail.append("crowded")
            if not _trend: _fail.append("trend")
            logger.info(f"[NEAR-MISS] {metadata['pair']} score={_score} candle={_candle} "
                           f"fail=[{','.join(_fail)}] d={_discount} r={_rsi} c={_cvd} o={_oi} "
                           f"f={_fund} l={_liq} t={_trend} ch={_chop} at={_atr} cr={_crowd}")
        # --- END NEAR-MISS ---

        # Shotgun guard: max 2 entries per 5m candle (only current candle, not historical)
        _candle_ts = int(dataframe.iloc[-1]["date"].timestamp())
        _key = f"_last_entry_candle"
        if not hasattr(self, _key):
            setattr(self, _key, {})
        _tracker = getattr(self, _key)
        # Clean old candles (>10 min ago)
        _tracker = {ts: cnt for ts, cnt in _tracker.items() if _candle_ts - ts <= 600}
        _this_candle_count = _tracker.get(_candle_ts, 0)

        # Only allow entry on current candle if we haven't hit the limit
        if _this_candle_count >= 2 or not bool(cond.iloc[-1]):
            # Mask all (including current candle)
            cond = cond & False
        else:
            # Allow current candle entry
            _tracker[_candle_ts] = _this_candle_count + 1
            # Keep only current candle True, mask all historical
            cond = cond & False
            cond.iloc[-1] = True

        setattr(self, _key, _tracker)

        # Set enter_long only on current candle
        if bool(cond.iloc[-1]):
            dataframe.loc[dataframe.index[-1], "enter_long"] = 1
            dataframe.loc[dataframe.index[-1], "enter_tag"] = "revo_adaptive_v1"

        self._audit_entry_signal(dataframe, metadata, cond)
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["exit_long"] = 0
        return dataframe

    def leverage(self, pair: str, current_time: datetime, current_rate: float, proposed_leverage: float,
                 max_leverage: float, entry_tag: Optional[str], side: str, **kwargs) -> float:
        return min(1.0, max_leverage)