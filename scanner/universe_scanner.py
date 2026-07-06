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
    rate_burst: int = 10      # max concurrent requests per batch
    batch_delay: float = 0.1  # seconds between batches


# Binance rate limit: 1200/min for GET endpoints
# Kline endpoint: GET /fapi/v1/klines?symbol=BTCUSDT&interval=5m&limit=100
# OI endpoint: GET /fapi/v1/openInterest?symbol=BTCUSDT
# Combined: 2 calls per pair, 530 pairs = 1060 calls
# At 500 calls/min safe rate: ~2.1 minutes max, ~30 seconds with parallel


class UniverseScanner:
    """
    Fast parallel scanner for Binance Futures.
    
    Fetches per pair:
    - OHLCV for configured timeframes (30m, 5m, 1m, 1h)
    - Open Interest (current)
    - Funding rate (mark price endpoint)
    
    Designed for 530 pairs in < 60 seconds.
    """
    
    TOP_KLINES = {           # bins needed per timeframe
        "30m": 200,          # M30: 200 bars for EMA calc + imbalance detection
        "5m": 300,           # 5m: for fill detection (60 bars), EMA, trend
        "1m": 100,           # 1m: fill detection wick sensitivity
        "1h": 100,           # H1: for trend confirmation
    }
    
    LIQUIDITY_MIN_VOLUME = 500_000  # Minimum 24h volume to consider pair liquid
    
    def __init__(self, cache_dir: str = None):
        self.cache_dir = Path(cache_dir or Path(__file__).parent.parent / "data")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.client: Optional[httpx.AsyncClient] = None
        self.pairs: List[str] = []
        self.oi_collector: Optional[OICollector] = None
    
    async def _start(self):
        self.client = httpx.AsyncClient(
            timeout=15.0,
            limits=httpx.Limits(max_connections=100, max_keepalive_connections=50)
        )
        self.oi_collector = OICollector(exchanges=["binance"])
    
    async def _close(self):
        if self.client:
            await self.client.aclose()
        if self.oi_collector:
            await self.oi_collector.shutdown()
    
    async def discover_pairs(self, min_volume_24h: float = 0) -> List[str]:
        """Discover all Binance USDT perps, optionally filter by volume."""
        await self._start()
        cfg = EXCHANGES["binance"]
        all_pairs = await fetch_symbols(cfg)
        print(f"[scanner] Binance total: {len(all_pairs)} pairs")
        
        if min_volume_24h > 0:
            # Filter by 24h volume (requires fetching ticker data)
            liquid = await self._filter_liquid(all_pairs, min_volume_24h)
            print(f"[scanner] After volume filter {min_volume_24h:,.0f}: {len(liquid)} pairs")
            self.pairs = liquid
        else:
            self.pairs = all_pairs
        
        return self.pairs
    
    async def _filter_liquid(self, pairs: List[str], min_vol: float) -> List[str]:
        """Filter pairs that meet 24h volume threshold."""
        # Use 24hr ticker endpoint: batch of symbols
        ticker_url = "https://fapi.binance.com/fapi/v1/ticker/24hr"
        resp = await self.client.get(ticker_url)
        resp.raise_for_status()
        tickers = resp.json()
        
        vol_map = {}
        if isinstance(tickers, dict):  # single symbol
            vol_map[tickers["symbol"]] = float(tickers["quoteVolume"])
        else:
            for t in tickers:
                if isinstance(t, dict) and "symbol" in t:
                    vol_map[t["symbol"]] = float(t.get("quoteVolume", 0))
        
        liquid = [p for p in pairs if vol_map.get(p, 0) >= min_vol]
        return liquid
    
    async def fetch_kline_batch(self, pairs: List[str], interval: str,
                                 limit: int, batch_size: int = 20) -> Dict[str, pd.DataFrame]:
        """
        Fetch klines for many pairs in parallel batches.
        
        Returns: {symbol: DataFrame with OHLCV columns}
        """
        results = {}
        kline_url = "https://fapi.binance.com/fapi/v1/klines"
        
        # Split into batches to respect rate limits
        for i in range(0, len(pairs), batch_size):
            batch = pairs[i:i + batch_size]
            
            tasks = []
            for symbol in batch:
                params = {"symbol": symbol, "interval": interval, "limit": limit}
                tasks.append(self._fetch_kline_single(kline_url, params, symbol))
            
            batch_results = await asyncio.gather(*tasks, return_exceptions=True)
            
            for symbol, result in zip(batch, batch_results):
                if isinstance(result, Exception):
                    continue
                if result is not None:
                    results[symbol] = result
            
            # Small delay between batches
            if i + batch_size < len(pairs):
                safe_delay = max(len(batch) / 500, 0.05)  # 500 req/min safe
                await asyncio.sleep(safe_delay)
        
        return results
    
    async def _fetch_kline_single(self, url: str, params: dict, 
                                   symbol: str) -> Optional[pd.DataFrame]:
        """Fetch single kline endpoint, return DataFrame."""
        try:
            resp = await self.client.get(url, params=params)
            resp.raise_for_status()
            raw = resp.json()
            
            if not raw or not isinstance(raw, list):
                return None
            
            # Binance kline format: [open_time, open, high, low, close, volume, 
            #   close_time, quote_vol, trades, taker_buy_base, taker_buy_quote, ignore]
            data = []
            for k in raw:
                try:
                    data.append({
                        "open_time": float(k[0]),
                        "open": float(k[1]),
                        "high": float(k[2]),
                        "low": float(k[3]),
                        "close": float(k[4]),
                        "volume": float(k[5]),
                        "close_time": float(k[6]),
                        "quote_vol": float(k[7]),
                        "trades": float(k[8]),
                        "taker_buy_base": float(k[9]),
                        "taker_buy_quote": float(k[10]),
                    })
                except (TypeError, ValueError, IndexError):
                    continue
            
            df = pd.DataFrame(data)
            if df.empty:
                return None
            
            df["open_time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
            df.set_index("open_time", inplace=True)
            df.sort_index(inplace=True)
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
        
        # Step 2: Fetch OI (parallel to klines, already started)
        if with_oi and self.oi_collector:
            print(f"[scanner] Fetching OI for {len(pairs)} pairs...")
            oi_results = await self.oi_collector.collect_all(
                symbols=pairs, batch_size=20
            )
            result["oi"] = oi_results
            oi_count = sum(1 for d in oi_results.values() if d.get("binance"))
            print(f"[scanner] OI: {oi_count}/{len(pairs)} pairs have Binance OI")
        
        elapsed = time.time() - t0
        result["scan_time_s"] = elapsed
        print(f"[scanner] Full scan complete in {elapsed:.1f}s")
        
        return result
    
    def save_cache(self, scan_result: Dict, prefix: str = "latest"):
        """Save scan results to disk cache."""
        ts = int(scan_result["ts"])
        cache_path = self.cache_dir / f"{prefix}_scan_{ts}.json"
        
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