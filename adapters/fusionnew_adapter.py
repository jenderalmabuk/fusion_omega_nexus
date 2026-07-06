"""
fusion_omega_nexus/adapters/fusionnew_adapter.py
Bridge between nexus scanner cache and fusionnew engine.
Provides ohclv data from nexus universal scan instead of per-symbol API calls.

Usage in fusionnew engine:
    from adapters.fusionnew_adapter import NexusFeed
    feed = NexusFeed(cache_dir="/path/to/nexus/data")
    
    # Replace fetch_recent with:
    zone_df = feed.get_ohlcv(symbol, "30m", 300)
    ltf = feed.get_ohlcv(symbol, "5m", 1000)
    
    # Bonus: multi-exchange OI for flow filtering:
    oi_change = feed.get_oi_delta(symbol, lookback=16)
"""

import pandas as pd
import json
import time
from pathlib import Path
from typing import Dict, Optional, List
from collections import deque


class NexusFeed:
    """
    Reads cached OHLCV + OI data from nexus universal scan.
    
    Cache format (from universe_scanner.py):
        data/
          latest_scan_{ts}.json       — summary with OI per pair
          30m/{SYMBOL}_{ts}.parquet   — OHLCV parquet per pair
          5m/...
          1m/...
          1h/...
    
    Provides same interface as fetch_recent() for drop-in replacement.
    """
    
    def __init__(self, cache_dir: str = None):
        self.cache_dir = Path(cache_dir or "/home/fusion_omega/fusion_omega_nexus/data")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        
        # In-memory cache
        self._kline_cache: Dict[str, Dict[str, pd.DataFrame]] = {}  # {symbol: {tf: df}}
        self._oi_cache: Dict[str, Dict] = {}
        self._oi_history: Dict[str, deque] = {}
        
        # Cache freshness tracking
        self._last_scan_ts: float = 0
        self._scan_interval: float = 60  # seconds — re-read if cache older than this
    
    def _find_latest_scan(self) -> Optional[Path]:
        """Find the most recent scan summary file."""
        summaries = sorted(
            self.cache_dir.glob("latest_scan_*.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True
        )
        return summaries[0] if summaries else None
    
    def _load_scan(self) -> bool:
        """Load latest scan results from disk cache."""
        summary_path = self._find_latest_scan()
        if not summary_path:
            print("[nexus_feed] No cached scan found")
            return False
        
        scan_ts = summary_path.stat().st_mtime
        if scan_ts <= self._last_scan_ts:
            return True  # already loaded
        
        try:
            with open(summary_path) as f:
                summary = json.load(f)
        except Exception as e:
            print(f"[nexus_feed] Failed to load summary: {e}")
            return False
        
        self._last_scan_ts = scan_ts
        self._oi_cache = summary.get("oi", {})
        
        # Load per-timeframe parquet files
        tfs = summary.get("timeframes_present", [])
        for tf in tfs:
            tf_dir = self.cache_dir / tf
            if not tf_dir.exists():
                continue
            
            for parquet_file in tf_dir.glob(f"*_{int(summary['ts'])}.parquet"):
                symbol = parquet_file.name.split("_")[0]
                try:
                    df = pd.read_parquet(parquet_file)
                    if symbol not in self._kline_cache:
                        self._kline_cache[symbol] = {}
                    self._kline_cache[symbol][tf] = df
                except Exception:
                    pass
        
        print(f"[nexus_feed] Loaded scan ts={int(scan_ts)}: OI={len(self._oi_cache)} pairs, "
              f"klines={sum(len(v) for v in self._kline_cache.values())} TF-symbol combos")
        return True
    
    def get_ohlcv(self, symbol: str, interval: str, limit: int = 300) -> pd.DataFrame:
        """
        Get OHLCV DataFrame for a symbol.
        Mimics fetch_recent(limit) interface.
        
        Args:
            symbol: e.g., "BTCUSDT"
            interval: "30m", "5m", "1m", "1h", "4h"
            limit: how many recent bars (default 300)
        
        Returns:
            DataFrame with columns: open_time, open, high, low, close, volume, taker_buy_base
            Empty DataFrame if symbol not cached.
        """
        # Try loading cache if not loaded
        if not self._last_scan_ts:
            self._load_scan()
        elif time.time() - self._last_scan_ts > self._scan_interval:
            self._load_scan()
        
        if symbol not in self._kline_cache:
            return pd.DataFrame()
        
        tf_data = self._kline_cache[symbol].get(interval)
        if tf_data is None or tf_data.empty:
            return pd.DataFrame()
        
        # Return last `limit` bars
        df = tf_data.iloc[-limit:].copy()
        
        # Ensure columns match what engine expects
        expected_cols = ["open", "high", "low", "close", "volume", "taker_buy_base"]
        for col in expected_cols:
            if col not in df.columns:
                if col == "taker_buy_base":
                    df[col] = 0.0  # fill with 0 if missing
                else:
                    return pd.DataFrame()  # critical column missing
        
        # Rename open_time to match engine expectation (index)
        if "open_time" in df.columns:
            df["open_time"] = pd.to_datetime(df["open_time"])
        elif df.index.name == "open_time":
            df = df.reset_index()
        
        return df[["open_time"] + expected_cols]
    
    def get_oi(self, symbol: str) -> float:
        """
        Get aggregated Open Interest for a symbol.
        Returns 0 if not available.
        """
        if not self._oi_cache:
            self._load_scan()
        
        oi_data = self._oi_cache.get(symbol, {})
        binance_oi = oi_data.get("binance")
        bybit_oi = oi_data.get("bybit")
        
        # Simple aggregation: prefer both, fallback to available
        if binance_oi and bybit_oi:
            agg = (binance_oi + bybit_oi) / 2
        elif binance_oi:
            agg = binance_oi
        elif bybit_oi:
            agg = bybit_oi
        else:
            agg = 0.0
        
        return agg
    
    def get_oi_delta(self, symbol: str, lookback: int = 16) -> float:
        """
        Get OI change percentage over lookback samples.
        Positive = OI increasing, Negative = OI decreasing.
        """
        if symbol not in self._oi_history:
            self._oi_history[symbol] = deque(maxlen=60)
        
        current = self.get_oi(symbol)
        if current > 0:
            self._oi_history[symbol].append(current)
        
        hist = list(self._oi_history[symbol])
        if len(hist) < lookback + 1:
            return 0.0
        
        now = hist[-1]
        past = hist[-lookback]
        if past <= 0:
            return 0.0
        
        return ((now - past) / past) * 100.0
    
    def get_cvd_proxy(self, symbol: str, interval: str = "5m", window: int = 5) -> float:
        """
        CVD proxy from taker buy volume delta over recent window.
        Positive = buying pressure, Negative = selling pressure.
        """
        df = self.get_ohlcv(symbol, interval, limit=window + 1)
        if df.empty or "taker_buy_base" not in df.columns:
            return 0.0
        
        recent = df.iloc[-window:]
        taker_buy = recent["taker_buy_base"].sum()
        total_vol = recent["volume"].sum()
        
        if total_vol <= 0:
            return 0.0
        
        # CVD proxy = cumulative taker buy - taker sell (approximated)
        taker_sell = total_vol - taker_buy
        return (taker_buy - taker_sell) / total_vol  # normalized
    
    def available_pairs(self) -> List[str]:
        """List all pairs currently cached."""
        if not self._kline_cache:
            self._load_scan()
        return sorted(self._kline_cache.keys())
    
    def cache_age(self) -> float:
        """Age of current cache in seconds."""
        if not self._last_scan_ts:
            return float('inf')
        return time.time() - self._last_scan_ts


# ---------------------------------------------------------------------------
# Quick test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    feed = NexusFeed()
    
    # Test: check if any cached data exists
    pairs = feed.available_pairs()
    print(f"Cached pairs: {len(pairs)}")
    
    if pairs:
        test_pair = pairs[0]
        df_30m = feed.get_ohlcv(test_pair, "30m", 50)
        oi = feed.get_oi(test_pair)
        print(f"\n{test_pair}:")
        print(f"  M30 bars: {len(df_30m)}")
        print(f"  OI: {oi:,.0f}")
        print(f"  Cache age: {feed.cache_age():.0f}s")
    else:
        print("No cached data yet. Run universe_scanner.py first.")