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
    
    async def _start(self):
        self.client = httpx.AsyncClient(
            timeout=15.0,
            limits=httpx.Limits(max_connections=50, max_keepalive_connections=20)
        )
        self.oi_collector = OICollector(exchanges=["bybit"])
    
    async def discover_pairs(self, min_volume_24h: float = 0) -> List[str]:
        """Discover all Bybit USDT perps, optionally filter by volume."""
        await self._start()
        cfg = EXCHANGES[self.primary_exchange]
        all_pairs = await fetch_symbols(cfg)
        print(f"[scanner] Bybit total: {len(all_pairs)} pairs")
        
        # Bybit volume filter is part of ticker endpoint, requires iterating
        if min_volume_24h > 0:
            liquid = await self._filter_liquid_bybit(all_pairs, min_volume_24h)
            print(f"[scanner] After volume filter {min_volume_24h:,.0f}: {len(liquid)} pairs")
            self.pairs = liquid
        else:
            self.pairs = all_pairs
        
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

    async def fetch_kline_batch(self, pairs: List[str], interval: str,
                                 limit: int, batch_size: int = 5) -> Dict[str, pd.DataFrame]:
        """
        Fetch klines for many pairs in parallel batches from Bybit.
        
        Returns: {symbol: DataFrame with OHLCV columns}
        """
        results = {}
        # Bybit needs interval in minutes as string: "60" for 1h, "1" for 1m
        bybit_interval = interval.replace('m','').replace('h','*60')
        if '*' in bybit_interval:
            parts = bybit_interval.split('*')
            bybit_interval = str(int(parts[0]) * int(parts[1]))

        for i in range(0, len(pairs), batch_size):
            batch = pairs[i:i + batch_size]
            
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
            
            # Rate limit: 100/min → ~1.7 req/s
            await asyncio.sleep(max(len(batch) / 1.5, 0.7))
        
        return results
    
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
        
        # Wait for all kline tasks
        for tf, task in kline_tasks.items():
            try:
                data = await task
                success = len(data)
                result["timeframes"][tf] = data
                print(f"[scanner] {tf}: {success}/{len(pairs)} pairs fetched")
            except Exception as e:
                print(f"[scanner] {tf}: ERROR {e}")
                result["timeframes"][tf] = {}
        
        # Step 2: Fetch OI (from Bybit since Binance REST banned us)
        if with_oi and self.oi_collector:
            print(f"[scanner] Fetching OI via Bybit for {len(pairs)} pairs (Binance banned)...")
            # Only use Bybit — Binance REST banned at our request rate
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
        
        print(f"[scanner] Cache saved to {self.cache_dir}")


# ---------------------------------------------------------------------------
# CLI mode
# ---------------------------------------------------------------------------

async def _cli():
    scanner = UniverseScanner()
    
    # Discover and filter
    await scanner.discover_pairs(min_volume_24h=0)  # take all pairs
    print(f"\n[scanner] Ready: {len(scanner.pairs)} pairs in queue")
    print(f"[scanner] Estimated scan time: ~{len(scanner.pairs)/15:.0f}s")
    
    want = input("\nFull scan? (y/N): ").strip().lower()
    if want == 'y':
        result = await scanner.full_scan()
        scanner.save_cache(result)
    
    await scanner._close()

if __name__ == "__main__":
    asyncio.run(_cli())