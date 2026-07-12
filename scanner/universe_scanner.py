"""
fusion_omega_nexus/scanner/universe_scanner.py
Fast universal scanner: fetch OHLCV + OI for full universe across Binance.
Design goal: full 530-pair scan in < 30 seconds.
"""

import asyncio
import httpx
import time
import numpy as np
import pandas as pd
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass

# Import from sibling core module
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from core.oi_collector import OICollector, EXCHANGES, fetch_symbols


@dataclass
class ScannerConfig:
    """Scan configuration per timeframe."""
    bins: Dict[str, int]      # {tf: candle_count}
    rate_burst: int = 5      # max concurrent requests per batch
    batch_delay: float = 0.1  # seconds between batches


# Binance rate limit: 1200/min for GET endpoints
# Kline endpoint: GET /fapi/v1/klines?symbol=BTCUSDT&interval=5m&limit=100
# OI endpoint: GET /fapi/v1/openInterest?symbol=BTCUSDT
# Combined: 2 calls per pair, 530 pairs = 1060 calls
# At 500 calls/min safe rate: ~2.1 minutes max, ~30 seconds with parallel


class UniverseScanner:
    """
    Fast parallel scanner for Bybit Futures (Binance fallback).
    Binance IP is banned, so we now use Bybit as primary.

    Fetches per pair:
    - OHLCV for configured timeframes (30m, 5m, 1m, 1h)
    - Open Interest (current)
    - Funding rate

    Designed for ~431 pairs. Expected speed: ~6-8 minutes.
    """
    TOP_KLINES = {           # bins needed per timeframe
        "30m": 260,          # M30: 260 bars for EMA calc + imbalance detection
        "5m": 300,           # 5m: for fill detection (60 bars), EMA, trend
        "1m": 100,           # 1m: fill detection wick sensitivity
        "1h": 100,           # H1: for trend confirmation
    }
    
    # Bybit rate limit: 100/min for GET endpoints
    # kline endpoint: /v5/market/klines?category=linear&symbol=BTCUSDT&interval=60&limit=200
    
    LIQUIDITY_MIN_VOLUME = 500_000  # Minimum 24h volume
    
    def __init__(self, cache_dir: str = None):
        self.cache_dir = Path(cache_dir or Path(__file__).parent.parent / "data")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.client: Optional[httpx.AsyncClient] = None
        self.pairs: List[str] = []
        self.oi_collector: Optional[OICollector] = None
        self.primary_exchange = "bybit"  # Fallback to Bybit
        self.binance_only_symbols: set = set()
    
    async def _start(self):
        self.client = httpx.AsyncClient(
            timeout=15.0,
            limits=httpx.Limits(max_connections=50, max_keepalive_connections=20)
        )
        # Collaborative: Binance OI (richer data) + Bybit OI fallback
        self.oi_collector = OICollector(exchanges=["binance", "bybit"])
    
    async def discover_pairs(self, min_volume_24h: float = 0) -> List[str]:
        """Discover pairs: Bybit ∩ Binance intersection, sort by |price change|."""
        await self._start()
        
        # Fetch both exchange symbol lists
        bybit_pairs = set(await fetch_symbols(EXCHANGES["bybit"]))
        binance_pairs = set(await fetch_symbols(EXCHANGES["binance"]))
        
        # Intersection: pairs on BOTH exchanges
        intersection = sorted(bybit_pairs & binance_pairs)
        # Also include Binance-only pairs that have volume (for OI/klines fallback)
        binance_only = sorted(binance_pairs - bybit_pairs)
        self.binance_only_symbols = set(binance_only)
        print(f"[scanner] Bybit: {len(bybit_pairs)} | Binance: {len(binance_pairs)} | Both: {len(intersection)} | Binance-only: {len(binance_only)}")
        
        # Sort by |24h price change| descending using Bybit tickers for intersection
        r = await self.client.get("https://api.bybit.com/v5/market/tickers?category=linear")
        tickers = r.json().get("result", {}).get("list", [])
        pct_map = {}
        vol_map = {}
        for t in tickers:
            sym = t["symbol"]
            if sym in intersection:
                pct_map[sym] = abs(float(t.get("price24hPcnt", 0)))
                vol_map[sym] = float(t.get("turnover24h", 0))
        
        # For Binance-only pairs, fetch volume from Binance
        binance_vol_map = {}
        for sym in binance_only[:50]:  # limit to top 50 to avoid rate limit
            try:
                r = await self.client.get(f"https://fapi.binance.com/fapi/v1/ticker/24hr?symbol={sym}")
                data = r.json()
                binance_vol_map[sym] = float(data.get("quoteVolume", 0))
            except Exception:
                pass
        
        # Filter intersection by volume
        qualified = [s for s in intersection if vol_map.get(s, 0) >= min_volume_24h]
        qualified.sort(key=lambda s: pct_map.get(s, 0), reverse=True)
        
        # Add Binance-only pairs with sufficient volume (top 20)
        binance_qualified = [s for s in binance_only if binance_vol_map.get(s, 0) >= min_volume_24h]
        binance_qualified.sort(key=lambda s: binance_vol_map.get(s, 0), reverse=True)
        qualified.extend(binance_qualified[:20])  # top 20 Binance-only by volume
        
        print(f"[scanner] After volume filter ${min_volume_24h:,.0f}: {len(qualified)} pairs ({len(intersection)} intersection + {len(binance_qualified[:20])} Binance-only)")
        print(f"[scanner] Top 5: {qualified[:5]}")
        self.pairs = qualified
        return self.pairs
    
    async def _filter_liquid_bybit(self, pairs: List[str], min_vol: float) -> List[str]:
        """Filter Bybit pairs by 24h volume."""
        # Bybit ticker endpoint /v5/market/tickers?category=linear&symbol=BTCUSDT
        liquid = []
        for i in range(0, len(pairs), 5):  # batch of 5
            batch = pairs[i:i+5]
            tasks = []
            for p in batch:
                url = f"https://api.bybit.com/v5/market/tickers?category=linear&symbol={p}"
                tasks.append(self.client.get(url))
            
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for p, r in zip(batch, results):
                if isinstance(r, Exception): continue
                try:
                    data = r.json()
                    if data.get('retCode') == 0:
                        vol = float(data['result']['list'][0]['turnover24h'])
                        if vol >= min_vol:
                            liquid.append(p)
                except Exception:
                    pass
            await asyncio.sleep(0.7) # be gentle
        return liquid
    
    async def _fetch_kline_single_bybit(self, url: str, params: dict, 
                                        symbol: str) -> Optional[pd.DataFrame]:
        """Fetch single kline endpoint for Bybit, return DataFrame."""
        try:
            resp = await self.client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()
            
            if data.get('retCode') != 0:
                return None
            
            raw = data['result']['list']
            if not raw or not isinstance(raw, list):
                return None
            
            # Bybit kline format: [start, open, high, low, close, volume, turnover]
            df_data = []
            for k in raw:
                df_data.append({
                    "open_time": float(k[0]),
                    "open": float(k[1]), "high": float(k[2]), "low": float(k[3]),
                    "close": float(k[4]), "volume": float(k[5]),
                })
            
            df = pd.DataFrame(df_data)
            if df.empty: return None
            
            df["open_time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
            df.set_index("open_time", inplace=True)
            df.sort_index(inplace=True, ascending=False) # Bybit returns newest first
            df = df.iloc[::-1] # reverse to oldest first
            
            # Add proxy for taker_buy_base
            df['taker_buy_base'] = df['volume'] * 0.5
            return df
            
        except Exception:
            return None
    
    async def _fetch_kline_single_binance(self, symbol: str, interval: str,
                                          limit: int) -> Optional[pd.DataFrame]:
        """Fetch single kline from Binance."""
        try:
            interval_map = {"5m": "5m", "30m": "30m", "1h": "1h", "1m": "1m"}
            params = {"symbol": symbol, "interval": interval_map.get(interval, interval), "limit": limit}
            resp = await self.client.get("https://fapi.binance.com/fapi/v1/klines", params=params)
            resp.raise_for_status()
            data = resp.json()
            
            if not data or not isinstance(data, list):
                return None
            
            df_data = []
            for k in data:
                df_data.append({
                    "open_time": float(k[0]),
                    "open": float(k[1]), "high": float(k[2]), "low": float(k[3]),
                    "close": float(k[4]), "volume": float(k[5]),
                })
            
            df = pd.DataFrame(df_data)
            if df.empty:
                return None
            
            df["open_time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
            df.set_index("open_time", inplace=True)
            df.sort_index(inplace=True)
            
            df['taker_buy_base'] = df['volume'] * 0.5
            return df
        except Exception:
            return None
    
    async def fetch_kline_batch(self, pairs: List[str], interval: str,
                                 limit: int, batch_size: int = 5) -> Dict[str, pd.DataFrame]:
        """
        Fetch klines for many pairs from Bybit (primary) + Binance (fallback for Binance-only symbols).
        """
        results = {}
        
        # Bybit needs interval in minutes as string
        bybit_interval = interval.replace('m','').replace('h','*60')
        if '*' in bybit_interval:
            parts = bybit_interval.split('*')
            bybit_interval = str(int(parts[0]) * int(parts[1]))
        
        # Split pairs: bybit_pairs (on Bybit) vs binance_only_pairs
        bybit_pairs = [p for p in pairs if p not in self.binance_only_symbols]
        binance_pairs = [p for p in pairs if p in self.binance_only_symbols]
        
        # Fetch from Bybit for bybit_pairs
        for i in range(0, len(bybit_pairs), batch_size):
            batch = bybit_pairs[i:i + batch_size]
            tasks = []
            for symbol in batch:
                params = {"category": "linear", "symbol": symbol, "interval": bybit_interval, "limit": limit}
                url = "https://api.bybit.com/v5/market/kline"
                tasks.append(self._fetch_kline_single_bybit(url, params, symbol))
            
            batch_results = await asyncio.gather(*tasks, return_exceptions=True)
            for symbol, result in zip(batch, batch_results):
                if isinstance(result, Exception): continue
                if result is not None:
                    results[symbol] = result
            
            await asyncio.sleep(max(len(batch) / 20, 0.1))
        
        # Fetch from Binance for binance_only_pairs
        for i in range(0, len(binance_pairs), batch_size):
            batch = binance_pairs[i:i + batch_size]
            tasks = []
            for symbol in batch:
                tasks.append(self._fetch_kline_single_binance(symbol, interval, limit))
            
            batch_results = await asyncio.gather(*tasks, return_exceptions=True)
            for symbol, result in zip(batch, batch_results):
                if isinstance(result, Exception): continue
                if result is not None:
                    results[symbol] = result
            
            await asyncio.sleep(0.1)
        
        return results
    
    async def full_scan(self, tfs: List[str] = None, pairs: List[str] = None,
                        with_oi: bool = True) -> Dict:
        """
        Full universe scan: OHLCV for multiple timeframes + OI.
        
        Returns: {
            "ts": timestamp,
            "pairs_scanned": int,
            "timeframes": {tf: {symbol: DataFrame}},
            "oi": {symbol: aggregated_oi},
            "scan_time_s": float,
        }
        """
        tfs = tfs or list(self.TOP_KLINES.keys())
        pairs = pairs or self.pairs
        
        await self._start()
        print(f"[scanner] Full scan: {len(pairs)} pairs, {len(tfs)} timeframes")
        t0 = time.time()
        
        result = {
            "ts": time.time(),
            "pairs_scanned": len(pairs),
            "timeframes": {},
            "oi": {},
        }
        
        # Step 1: Fetch OHLCV for each timeframe (parallel across timeframes)
        # Start all TFs concurrently, each TF runs in batches
        kline_tasks = {}
        for tf in tfs:
            limit = self.TOP_KLINES.get(tf, 100)
            print(f"[scanner] Starting: {len(pairs)} pairs @ {tf} ({limit} bars)")
            kline_tasks[tf] = asyncio.create_task(
                self.fetch_kline_batch(pairs, tf, limit, batch_size=20)
            )
        
        # Wait for all timeframes
        for tf, task in kline_tasks.items():
            frames = await task
            result["timeframes"][tf] = frames
            print(f"[scanner] {tf}: {len(frames)}/{len(pairs)} pairs fetched")
        
        # Step 2: Fetch OI for all pairs (collaborative Binance + Bybit)
        if with_oi:
            print(f"[scanner] Fetching OI for {len(pairs)} pairs...")
            # Use Binance + Bybit OI collector
            oi_results = await self.oi_collector.collect_all(
                symbols=pairs, batch_size=5  # smaller batch, longer delay for Bybit 100/min
            )
            # Filter: only accept pairs that have Binance OR Bybit OI
            for symbol, data in oi_results.items():
                oi_count = sum(1 for v in data.values() if v is not None and v > 0)
                if oi_count > 0:
                    result["oi"][symbol] = data
            oi_count = len(result["oi"])
            print(f"[scanner] OI: {oi_count}/{len(pairs)} pairs have OI from at least 1 exchange")
        
        elapsed = time.time() - t0
        result["scan_time_s"] = elapsed
        print(f"[scanner] Full scan complete in {elapsed:.1f}s")
        
        return result
    
    def save_cache(self, scan_result: Dict, prefix: str = "latest"):
        """Save scan results to disk cache."""
        ts = int(scan_result["ts"])
        cache_path = self.cache_dir / f"latest_scan_{ts}.json"
        
        # Save summary as JSON (DataFrames saved separately as parquet)
        summary = {
            "ts": scan_result["ts"],
            "pairs_scanned": scan_result["pairs_scanned"],
            "scan_time_s": scan_result["scan_time_s"],
            "oi": scan_result.get("oi", {}),
            "timeframes_present": list(scan_result["timeframes"].keys()),
        }
        
        with open(cache_path, "w") as f:
            import json
            json.dump(summary, f, default=str, indent=2)
        
        # Save per-timeframe parquet files
        for tf, frames in scan_result.get("timeframes", {}).items():
            tf_dir = self.cache_dir / tf
            tf_dir.mkdir(exist_ok=True)
            for symbol, df in frames.items():
                if df is not None and not df.empty:
                    parquet_path = tf_dir / f"{symbol}_{ts}.parquet"
                    df.to_parquet(parquet_path)
                    
                    # Auto-cleanup: delete old parquet files for this symbol
                    # Keep only the most recent one (the one we just wrote)
                    old_files = sorted(tf_dir.glob(f"{symbol}_*.parquet"))
                    if len(old_files) > 1:
                        for old in old_files[:-1]:  # all except the last (newest)
                            try:
                                old.unlink()
                            except Exception:
                                pass


# Singleton for orchestrator injection
_scanner_instance: Optional[UniverseScanner] = None


def get_scanner(cache_dir: str = None) -> UniverseScanner:
    """Get or create singleton scanner instance."""
    global _scanner_instance
    if _scanner_instance is None:
        _scanner_instance = UniverseScanner(cache_dir)
    return _scanner_instance