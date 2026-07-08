"""
NexusDataBridge — data layer for signal-copy pipeline.

Replaces AdvancedDataEngine (956 lines of Binance REST calls) with a lightweight
adapter that reads Nexus scanner cache (OHLCV parquet + OI JSON).

Interface compatibility: drop-in replacement for AdvancedDataEngine.get_advanced_metrics()
"""
from __future__ import annotations

import glob
import json
import time
from pathlib import Path
from typing import Any, Dict, Optional

import pandas as pd
import numpy as np


class NexusDataBridge:
    """
    Lightweight data adapter: reads scanner cache, computes derived metrics.
    
    Replaces 956-line AdvancedDataEngine with 30-50 lines of cache reads.
    """
    
    def __init__(self, cache_dir: str = None):
        self.cache_dir = Path(cache_dir or Path(__file__).parent.parent / "data")
        if not self.cache_dir.exists():
            raise FileNotFoundError(f"Scanner cache dir not found: {self.cache_dir}")
        
        # Cache latest scan metadata in memory (TTL 60s)
        self._scan_cache: Optional[Dict] = None
        self._scan_cache_ts: float = 0.0
        self._scan_cache_ttl: float = 60.0
    
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
    
    def _load_klines(self, symbol: str, tf: str, limit: int = 100) -> Optional[pd.DataFrame]:
        """Load OHLCV from parquet cache."""
        # Find most recent parquet for this symbol + tf
        tf_dir = self.cache_dir / tf
        if not tf_dir.exists():
            return None
        
        parquets = sorted(glob.glob(str(tf_dir / f"{symbol}_*.parquet")), reverse=True)
        if not parquets:
            return None
        
        df = pd.read_parquet(parquets[0])
        if df.empty:
            return None
        
        # Return last N bars
        return df.tail(limit).copy()
    
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
        """
        Proxy CVD z-score from volume distribution.
        
        Nexus scanner provides taker_buy_base (proxy: volume * 0.5).
        Real CVD = cumsum(buy_volume - sell_volume).
        We estimate: if close > open → buy pressure, else sell pressure.
        """
        if df.empty or len(df) < 20:
            return 0.0
        
        # Directional volume: green candles = buy, red = sell
        df = df.copy()
        df['directional_vol'] = df['volume'] * np.where(df['close'] > df['open'], 1, -1)
        
        # Cumulative delta
        cvd = df['directional_vol'].cumsum()
        
        # Z-score of last 20 bars
        mean = cvd.tail(20).mean()
        std = cvd.tail(20).std()
        if std == 0:
            return 0.0
        
        z = (cvd.iloc[-1] - mean) / std
        return float(z)
    
    def _calc_oi_changes(self, symbol: str, scan_data: Dict) -> Dict[str, float]:
        """
        Calculate OI changes from current + historical scans.
        
        Current implementation: single snapshot (no history yet).
        Future: store last N scans to calc 15m/1h deltas.
        """
        oi_dict = scan_data.get("oi", {}).get(symbol, {})
        if not oi_dict:
            return {"oi_change_15m_pct": 0.0, "oi_change_1h_pct": 0.0}
        
        # TODO: implement historical OI tracking
        # For now, return neutral (scanner runs every 60s, so we'd need 15+ past runs)
        return {
            "oi_change_15m_pct": 0.0,  # placeholder
            "oi_change_1h_pct": 0.0,    # placeholder
        }
    
    async def get_advanced_metrics(self, symbol: str) -> Dict[str, Any]:
        """
        Drop-in replacement for AdvancedDataEngine.get_advanced_metrics().
        
        Returns dict with fields expected by validation_engine.py:
        - price, oi_change_15m_pct, oi_change_1h_pct, cvd_zscore, imbalance,
        - funding_rate, rsi, price_change_15m_pct, regime_label
        """
        scan_data = self._load_latest_scan()
        
        # Load 5m klines (most recent bars for price + RSI)
        df_5m = self._load_klines(symbol, "5m", limit=100)
        if df_5m is None or df_5m.empty:
            # No data — return empty metrics (validator will handle gracefully)
            return {
                "symbol": symbol,
                "price": 0.0,
                "source": "nexus_cache",
                "cache_age_sec": 0.0,
            }
        
        # Current price
        price = float(df_5m['close'].iloc[-1])
        
        # RSI (14-period on 5m)
        rsi = self._calc_rsi(df_5m['close'], period=14)
        
        # CVD proxy
        cvd_zscore = self._calc_cvd_proxy(df_5m)
        imbalance = cvd_zscore / 3.0  # scale to match old imbalance field
        
        # OI changes (placeholder for now)
        oi_metrics = self._calc_oi_changes(symbol, scan_data)
        
        # Price change 15m (last 3 bars of 5m = 15 minutes)
        if len(df_5m) >= 4:
            p_15m_ago = float(df_5m['close'].iloc[-4])
            price_change_15m_pct = ((price - p_15m_ago) / p_15m_ago) * 100.0
        else:
            price_change_15m_pct = 0.0
        
        # Regime detection: simple volatility-based
        # ATR% over last 20 bars
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
        
        # Funding rate: load from 1h klines metadata (Bybit includes funding in ticker)
        # For now, placeholder (scanner doesn't fetch funding separately yet)
        funding_rate = 0.0001  # neutral placeholder
        
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
