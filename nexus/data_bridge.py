"""
NexusDataBridge — data layer for signal-copy pipeline.

Replaces AdvancedDataEngine (956 lines of Binance REST calls) with a lightweight
adapter that reads Nexus scanner cache (OHLCV parquet + OI JSON).

Interface compatibility: drop-in replacement for AdvancedDataEngine.get_advanced_metrics()
"""
from __future__ import annotations

import glob
import json
import os
import time
from pathlib import Path
from typing import Any, Dict, Optional

import aiohttp
import pandas as pd
import numpy as np

# Max scans to keep in memory for OI delta calculation
_HISTORY_SCAN_COUNT = 90  # 90 scans = ~90 min at 60s intervals


class NexusDataBridge:
    """
    Lightweight data adapter: reads scanner cache, computes derived metrics.
    
    Replaces 956-line AdvancedDataEngine with ~50 lines of cache reads.
    """

    def __init__(self, cache_dir: str = None, api_url: str = None):
        # Default to runtime/whales inside container (mounted from host)
        self.cache_dir = Path(cache_dir or Path(__file__).parent.parent / "runtime" / "whales")
        if not self.cache_dir.exists():
            # Fallback: try /app/data (legacy)
            fallback = Path("/app/data")
            if fallback.exists():
                self.cache_dir = fallback
            else:
                # Try the host's data directory
                host_data = Path(__file__).parent.parent / "data"
                if host_data.exists():
                    self.cache_dir = host_data
                else:
                    raise FileNotFoundError(f"Scanner cache dir not found: {self.cache_dir}")

        # FastAPI URL for klines (reads from TimescaleDB)
        self.api_url = api_url or os.getenv("NEXUS_API_URL", "http://fastapi:8000")

        # Cache latest scan metadata in memory (TTL 60s)
        self._scan_cache: Optional[Dict] = None
        self._scan_cache_ts: float = 0.0
        self._scan_cache_ttl: float = 60.0

        # Historical OI snapshots for delta calc (lazy-loaded)
        self._oi_history: Optional[Dict[str, Dict[int, float]]] = None

    def _load_latest_scan(self) -> Dict:
        """Load latest scanner run metadata (OI + timestamp)."""
        now = time.time()
        if self._scan_cache and (now - self._scan_cache_ts) < self._scan_cache_ttl:
            return self._scan_cache

        # Find most recent scan JSON
        jsons = sorted(glob.glob(str(self.cache_dir / "latest_scan_*.json")), reverse=True)
        if not jsons:
            return {}

        with open(jsons[0], "r") as f:
            data = json.load(f)

        self._scan_cache = data
        self._scan_cache_ts = now
        return data

    async def _fetch_klines_from_api(self, symbol: str, tf: str, limit: int = 100) -> Optional[pd.DataFrame]:
        """Fetch OHLCV from FastAPI (TimescaleDB)."""
        try:
            url = f"{self.api_url}/klines/binance/{symbol}"
            params = {"tf": tf, "limit": limit}
            async with aiohttp.ClientSession() as session:
                async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if data.get("data"):
                            df = pd.DataFrame(data["data"])
                            # Rename columns to match expected format
                            df = df.rename(columns={
                                "open_time": "timestamp",
                                "open": "open",
                                "high": "high",
                                "low": "low",
                                "close": "close",
                                "volume": "volume",
                            })
                            return df.tail(limit).copy()
        except Exception as e:
            pass
        return None

    def _load_klines(self, symbol: str, tf: str, limit: int = 100) -> Optional[pd.DataFrame]:
        """Load OHLCV from parquet cache or FastAPI."""
        # Try parquet cache first
        tf_dir = self.cache_dir / tf
        if tf_dir.exists():
            parquets = sorted(glob.glob(str(tf_dir / f"{symbol}_*.parquet")), reverse=True)
            if parquets:
                df = pd.read_parquet(parquets[0])
                if not df.empty:
                    return df.tail(limit).copy()
        # Fallback to FastAPI (runs in async context via get_advanced_metrics)
        return None

    def _calc_rsi(self, closes: pd.Series, period: int = 14) -> float:
        """Simple RSI calculation."""
        if len(closes) < period + 1:
            return 50.0

        delta = closes.diff()
        gain = delta.where(delta > 0, 0.0).rolling(window=period).mean()
        loss = -delta.where(delta < 0, 0.0).rolling(window=period).mean()

        rs = gain / loss.replace(0, 1e-10)
        rsi = 100 - (100 / (1 + rs))
        return float(rsi.iloc[-1]) if not pd.isna(rsi.iloc[-1]) else 50.0

    def _calc_cvd_proxy(self, df: pd.DataFrame) -> float:
        """Proxy CVD z-score from volume distribution."""
        if df.empty or len(df) < 20:
            return 0.0

        df = df.copy()
        df['directional_vol'] = df['volume'] * np.where(df['close'] > df['open'], 1, -1)
        cvd = df['directional_vol'].cumsum()

        mean = cvd.tail(20).mean()
        std = cvd.tail(20).std()
        if std == 0:
            return 0.0

        z = (cvd.iloc[-1] - mean) / std
        return float(z)

    def _build_oi_history(self) -> Dict[str, Dict[int, float]]:
        """Build in-memory OI history from last N scans for all symbols."""
        if self._oi_history is not None:
            return self._oi_history

        jsons = sorted(glob.glob(str(self.cache_dir / "latest_scan_*.json")))
        if len(jsons) < 2:
            self._oi_history = {}
            return self._oi_history

        # Use last ~90 scans (at 60s intervals = 90 min history)
        recent = jsons[-_HISTORY_SCAN_COUNT:]
        history: Dict[str, Dict[int, float]] = {}

        for path in recent:
            try:
                ts = int(path.split("_")[-1].replace(".json", ""))
                with open(path, "r") as f:
                    data = json.load(f)
                oi_map = data.get("oi", {})
                for sym, exchs in oi_map.items():
                    # Use first exchange available (Bybit or Binance)
                    total_oi = sum(float(v) for v in exchs.values())
                    if sym not in history:
                        history[sym] = {}
                    history[sym][ts] = total_oi
            except (json.JSONDecodeError, ValueError, KeyError):
                continue

        self._oi_history = history
        return history

    def _calc_oi_changes(self, symbol: str, scan_data: Dict) -> Dict[str, float]:
        """Calculate OI changes from historical scans.

        Uses in-memory OI history built from last ~90 scanner snapshots.
        Returns 0.0 deltas when no history is available.
        """
        history = self._build_oi_history()
        sym_history = history.get(symbol, {})
        if len(sym_history) < 2:
            return {"oi_change_15m_pct": 0.0, "oi_change_1h_pct": 0.0}

        # Current OI from latest scan
        oi_now = sum(
            float(v) for v in scan_data.get("oi", {}).get(symbol, {}).values()
        )
        if oi_now <= 0:
            return {"oi_change_15m_pct": 0.0, "oi_change_1h_pct": 0.0}

        timestamps = sorted(sym_history.keys())
        latest_ts = timestamps[-1]

        # Find closest snapshot to 15m ago (±2 min)
        target_15m = latest_ts - 900
        target_1h = latest_ts - 3600

        def _closest(target: int) -> Optional[int]:
            best = None
            best_diff = float("inf")
            for ts in timestamps:
                diff = abs(ts - target)
                if diff < best_diff and diff < 120:  # within 2 min tolerance
                    best_diff = diff
                    best = ts
            return best

        ts_15m = _closest(target_15m)
        ts_1h = _closest(target_1h)

        oi_15m_pct = 0.0
        oi_1h_pct = 0.0

        if ts_15m:
            oi_old = sym_history[ts_15m]
            if oi_old > 0:
                oi_15m_pct = ((oi_now - oi_old) / oi_old) * 100.0

        if ts_1h:
            oi_old = sym_history[ts_1h]
            if oi_old > 0:
                oi_1h_pct = ((oi_now - oi_old) / oi_old) * 100.0

        return {
            "oi_change_15m_pct": round(oi_15m_pct, 4),
            "oi_change_1h_pct": round(oi_1h_pct, 4),
        }

    async def get_advanced_metrics(self, symbol: str) -> Dict[str, Any]:
        """Drop-in replacement for AdvancedDataEngine.get_advanced_metrics()."""
        scan_data = self._load_latest_scan()

        # Load 5m klines (most recent bars for price + RSI) - try cache first, then API
        df_5m = self._load_klines(symbol, "5m", limit=100)
        if df_5m is None or df_5m.empty:
            df_5m = await self._fetch_klines_from_api(symbol, "5m", limit=100)
        if df_5m is None or df_5m.empty:
            return {
                "symbol": symbol,
                "price": 0.0,
                "source": "nexus_cache",
                "cache_age_sec": 0.0,
            }

        price = float(df_5m['close'].iloc[-1])
        rsi = self._calc_rsi(df_5m['close'], period=14)
        cvd_zscore = self._calc_cvd_proxy(df_5m)
        imbalance = cvd_zscore / 3.0

        oi_metrics = self._calc_oi_changes(symbol, scan_data)

        # Price change 15m
        if len(df_5m) >= 4:
            p_15m_ago = float(df_5m['close'].iloc[-4])
            price_change_15m_pct = ((price - p_15m_ago) / p_15m_ago) * 100.0
        else:
            price_change_15m_pct = 0.0

        # Regime detection
        if len(df_5m) >= 20:
            df_5m['hl'] = df_5m['high'] - df_5m['low']
            atr = df_5m['hl'].tail(20).mean()
            atr_pct = (atr / price) * 100.0

            if atr_pct > 1.5:
                regime = "HIGH_VOL"
            elif abs(price_change_15m_pct) > 0.8:
                regime = "TRENDING"
            else:
                regime = "RANGING"
        else:
            regime = "UNKNOWN"

        # Funding rate: extract from scan if available, else neutral
        funding_rate = float(scan_data.get("funding", {}).get(symbol, 0.0)) or 0.0001

        # Cache age
        scan_ts = scan_data.get("ts", 0.0)
        cache_age_sec = time.time() - scan_ts if scan_ts else 999.0

        return {
            "symbol": symbol,
            "price": price,
            "rsi": rsi,
            "cvd_zscore": cvd_zscore,
            "imbalance": imbalance,
            "funding_rate": funding_rate,
            "price_change_15m_pct": price_change_15m_pct,
            "regime_label": regime,
            "source": "nexus_cache",
            "cache_age_sec": cache_age_sec,
            **oi_metrics,
        }


# Singleton for orchestrator injection
_bridge_instance: Optional[NexusDataBridge] = None


def get_data_bridge(cache_dir: str = None) -> NexusDataBridge:
    """Get or create singleton bridge instance."""
    global _bridge_instance
    if _bridge_instance is None:
        _bridge_instance = NexusDataBridge(cache_dir)
    return _bridge_instance
