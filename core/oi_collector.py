"""
fusion_omega_nexus/core/oi_collector.py
Multi-exchange Open Interest collector (Binance + Bybit + OKX)
No API key required — all public endpoints.
Rate-limit-safe batch fetching with httpx async.

Reference: fusion archive data/oi_aggregator.py
Enhanced: no pybit dependency, httpx async, OKX support, rate-limit awareness
"""

import asyncio
import httpx
import time
import json
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from collections import deque
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Rate limit & batch config
# ---------------------------------------------------------------------------

@dataclass
class ExchangeConfig:
    name: str
    oi_url: str                          # template: {symbol} will be substituted
    symbols_url: str                      # endpoint to fetch all symbols
    rate_per_min: int                     # GET requests per minute
    rate_burst_per_sec: float             # avoid burst > this
    pairs: List[str] = field(default_factory=list)
    
    def oi_endpoint(self, symbol: str) -> str:
        return self.oi_url.format(symbol=symbol)

EXCHANGES = {
    "binance": ExchangeConfig(
        name="binance",
        oi_url="https://fapi.binance.com/fapi/v1/openInterest?symbol={symbol}",
        symbols_url="https://fapi.binance.com/fapi/v1/exchangeInfo",
        rate_per_min=1200,
        rate_burst_per_sec=20,
    ),
    "bybit": ExchangeConfig(
        name="bybit",
        oi_url="https://api.bybit.com/v5/market/open-interest?category=linear&symbol={symbol}&intervalTime=1h",
        symbols_url="https://api.bybit.com/v5/market/instruments-info?category=linear",
        rate_per_min=1200,  # public endpoints: 20 req/s
        rate_burst_per_sec=1.7,
    ),
    "okx": ExchangeConfig(
        name="okx",
        oi_url="https://www.okx.com/api/v5/public/open-interest?instId={symbol}",
        symbols_url="https://www.okx.com/api/v5/public/instruments?instType=SWAP",
        rate_per_min=120,   # generous estimate: 10/s for public endpoints
        rate_burst_per_sec=10,
    ),
}

# ---------------------------------------------------------------------------
# Symbol resolvers
# ---------------------------------------------------------------------------

async def fetch_symbols(exchange: ExchangeConfig) -> List[str]:
    """Fetch all USDⓈ-M perpetual pairs from exchange."""
    async with httpx.AsyncClient(timeout=15.0) as client:
        rows = []
        if exchange.name == "bybit":
            cursor = ""
            while True:
                params = {"category": "linear", "limit": "1000"}
                if cursor:
                    params["cursor"] = cursor
                resp = await client.get("https://api.bybit.com/v5/market/instruments-info", params=params)
                resp.raise_for_status()
                result = resp.json().get("result", {})
                rows.extend(result.get("list", []))
                cursor = result.get("nextPageCursor") or ""
                if not cursor:
                    break
            return [
                s["symbol"] for s in rows
                if s.get("contractType") == "LinearPerpetual"
                and s.get("quoteCoin") == "USDT"
                and s.get("status") == "Trading"
            ]

        resp = await client.get(exchange.symbols_url)
        resp.raise_for_status()
        data = resp.json()
    
    pairs = []
    if exchange.name == "binance":
        for s in data.get("symbols", []):
            if (s.get("contractType") == "PERPETUAL" and 
                s.get("quoteAsset") == "USDT" and
                s.get("status") == "TRADING"):
                pairs.append(s["symbol"])
    
    elif exchange.name == "okx":
        for s in data.get("data", []):
            if s["instId"].endswith("-USDT-SWAP") and s.get("state") == "live":
                pairs.append(s["instId"])
    
    return pairs


# ---------------------------------------------------------------------------
# OI fetcher (rate-limit-safe)
# ---------------------------------------------------------------------------

class OICollector:
    """
    Multi-exchange OI collector with rate-limit batching.
    
    Strategy:
    - Fetch all pairs' OI in batches based on per-exchange rate limits
    - Store results in memory with TTL cache
    - Aggregate OI across exchanges (avg, max, min, divergence detection)
    """
    
    def __init__(self, exchanges: List[str] = None, 
                 cache_ttl: int = 60, history_depth: int = 60):
        """
        Args:
            exchanges: which exchanges to collect from (default: all)
            cache_ttl: cache time-to-live in seconds
            history_depth: how many historical snapshots to keep per pair
        """
        self.exchange_names = exchanges or ["bybit"]  # default: bybit only (Binance REST bans at our volume)
        self.configs = {n: EXCHANGES[n] for n in self.exchange_names}
        self.cache_ttl = cache_ttl
        self.history_depth = history_depth
        self.client: Optional[httpx.AsyncClient] = None
        
        # Cache: {pair_norm: {exchange_name: oi_value, ts: timestamp}}
        self.cache: Dict[str, Dict[str, float]] = {}
        self.cache_ts: Dict[str, float] = {}
        
        # History: {pair_norm: deque of (ts, aggregated_oi)}
        self.history: Dict[str, deque] = {}
    
    async def _start(self):
        if self.client is None:
            self.client = httpx.AsyncClient(timeout=10.0, limits=httpx.Limits(
                max_connections=50, max_keepalive_connections=20))
    
    async def _close(self):
        if self.client:
            await self.client.aclose()
            self.client = None
    
    async def discover_pairs(self) -> Dict[str, List[str]]:
        """Discover all USDT perpetual pairs per exchange."""
        await self._start()
        results = {}
        for name, cfg in self.configs.items():
            try:
                pairs = await fetch_symbols(cfg)
                cfg.pairs = pairs
                results[name] = pairs
                print(f"[nexus] {name}: {len(pairs)} pairs discovered")
            except Exception as e:
                results[name] = []
                print(f"[nexus] {name}: ERROR {e}")
        return results
    
    def _rate_delay(self, exchange_name: str, batch_size: int) -> float:
        """Calculate safe delay between batches."""
        cfg = self.configs[exchange_name]
        # Conservative: use half of documented rate limit
        safe_rate = cfg.rate_per_min / 2
        batches_per_min = safe_rate / batch_size if batch_size > 0 else 1
        if batches_per_min <= 0:
            return 1.0
        return max(60.0 / batches_per_min, 0.1)
    
    async def _fetch_oi_batch(self, exchange_name: str, 
                               pairs: List[str], 
                               batch_size: int = 20) -> Dict[str, Optional[float]]:
        """
        Fetch OI for a batch of pairs with rate-limit-safe delay.
        Returns {symbol: oi_float or None if failed}
        """
        cfg = self.configs[exchange_name]
        results = {}
        
        for i in range(0, len(pairs), batch_size):
            batch = pairs[i:i + batch_size]
            
            if exchange_name == "okx":
                # OKX uses instId format: BTC-USDT-SWAP
                # If pairs are already in OKX format, use directly
                urls = [cfg.oi_endpoint(s.replace("USDT", "-USDT-SWAP")) for s in batch]
            else:
                urls = [cfg.oi_endpoint(s) for s in batch]
            
            # Parallel fetch within batch
            tasks = []
            for symbol, url in zip(batch, urls):
                tasks.append(self._fetch_single(url, symbol))
            
            batch_results = await asyncio.gather(*tasks, return_exceptions=True)
            
            for symbol, result in zip(batch, batch_results):
                if isinstance(result, Exception):
                    results[symbol] = None
                else:
                    results[symbol] = result
            
            # Rate-limit delay between batches
            if i + batch_size < len(pairs):
                delay = self._rate_delay(exchange_name, batch_size)
                await asyncio.sleep(delay)
        
        return results
    
    async def _fetch_single(self, url: str, symbol: str) -> Optional[float]:
        """Fetch single OI endpoint."""
        try:
            resp = await self.client.get(url)
            resp.raise_for_status()
            data = resp.json()
            
            # Parse per-exchange format
            # Binance: {"openInterest": "12345.678"}
            if "openInterest" in data:
                return float(data["openInterest"])
            
            # Bybit: {"retCode":0,"result":{"list":[{"openInterest":"12345.678",...}]}}
            if data.get("retCode") == 0:
                oi_list = data.get("result", {}).get("list", [])
                if oi_list:
                    return float(oi_list[0].get("openInterest", 0))
            
            # OKX: {"code":"0","data":[{"oi":"12345.678",...}]}
            if data.get("code") == "0":
                oi_data = data.get("data", [])
                if oi_data and isinstance(oi_data, list):
                    return float(oi_data[0].get("oi", 0))
            
            return None
        except Exception:
            return None
    
    async def collect_all(self, symbols: List[str] = None, 
                           batch_size: int = 20) -> Dict[str, Dict[str, Optional[float]]]:
        """
        Collect OI for all pairs across all exchanges.
        
        Args:
            symbols: if None, use all pairs from configs
            batch_size: how many symbols per batch
        
        Returns: {symbol_normalized: {"binance": oi, "bybit": oi, "okx": oi}}
        """
        await self._start()
        
        all_pairs = set()
        if symbols:
            all_pairs.update(symbols)
        else:
            for cfg in self.configs.values():
                all_pairs.update(cfg.pairs)
        
        # Use canonical Binance-style symbols (e.g., BTCUSDT)
        # Normalize: strip -USDT-SWAP suffix, keep USDT suffix
        normalized_pairs = sorted(
            {p.replace("-USDT-SWAP", "") for p in all_pairs}
        )
        
        results: Dict[str, Dict] = {}
        
        for exchange_name in self.exchange_names:
            cfg = self.configs[exchange_name]
            
            # Filter pairs available on this exchange
            if cfg.pairs:
                avail = [p for p in normalized_pairs if p in cfg.pairs 
                         or p.replace("USDT", "-USDT-SWAP") in cfg.pairs]
            else:
                avail = normalized_pairs
            
            if not avail:
                print(f"[nexus] {exchange_name}: 0 pairs avail (skip)")
                continue
            
            print(f"[nexus] {exchange_name}: fetching OI for {len(avail)} pairs "
                  f"(batch_size={batch_size}, rate_safe)")
            
            t0 = time.time()
            oi_data = await self._fetch_oi_batch(exchange_name, avail, batch_size)
            elapsed = time.time() - t0
            
            success = sum(1 for v in oi_data.values() if v is not None)
            print(f"[nexus] {exchange_name}: {success}/{len(avail)} fetched in {elapsed:.1f}s")
            
            for symbol, oi_val in oi_data.items():
                normalized = symbol.replace("-USDT-SWAP", "USDT") \
                                   .replace("USDTUSDT", "USDT")  \
                                   .replace("SWAPUSDT", "USDT")
                
                if normalized not in results:
                    results[normalized] = {}
                results[normalized][exchange_name] = oi_val
        
        return results
    
    def aggregate(self, per_exchange: Dict[str, Optional[float]], 
                  method: str = "avg") -> Tuple[float, str, int]:
        """
        Aggregate OI from multiple exchanges.
        
        Returns: (aggregated_value, source_label, exchange_count)
        
        Methods:
        - avg: simple average (default, robust)
        - min_verify: use average but flag high divergence
        - primary: prefer Binance, fallback Bybit, fallback OKX
        """
        valid = {k: v for k, v in per_exchange.items() if v is not None and v > 0}
        count = len(valid)
        
        if count == 0:
            return 0.0, "none", 0
        
        values = list(valid.values())
        avg_oi = sum(values) / count
        
        if count >= 2 and method == "min_verify":
            # Check divergence: if max/min > 1.5, flag it
            ratio = max(values) / min(values) if min(values) > 0 else 1.0
            if ratio > 1.5:
                source = f"{count}exch:DIVERGE({ratio:.2f}x)"
            else:
                source = f"{count}exch:converge"
        else:
            source = f"{count}exch"
        
        return avg_oi, source, count
    
    def compute_delta(self, symbol: str, lookback: int = 16) -> float:
        """
        Compute OI change percentage over lookback samples.
        Returns delta_pct (positive = OI increasing)
        """
        hist = self.history.get(symbol)
        if not hist or len(hist) < lookback:
            return 0.0
        
        oi_list = [h[1] for h in list(hist)]  # (ts, oi)
        now = oi_list[-1]
        past = oi_list[-lookback] if len(oi_list) >= lookback else oi_list[0]
        
        if past <= 0:
            return 0.0
        
        return ((now - past) / past) * 100.0
    
    def update_history(self, symbol: str, aggregated_oi: float):
        """Update rolling history for delta calculation."""
        if symbol not in self.history:
            self.history[symbol] = deque(maxlen=self.history_depth)
        self.history[symbol].append((time.time(), aggregated_oi))
    
    async def shutdown(self):
        await self._close()


# ---------------------------------------------------------------------------
# Quick test
# ---------------------------------------------------------------------------

async def _test():
    collector = OICollector(exchanges=["binance"])
    await collector.discover_pairs()
    binance_pairs = collector.configs["binance"].pairs[:5]
    print(f"\nTest: Binance OI for {binance_pairs}")
    
    results = await collector.collect_all(symbols=binance_pairs, batch_size=3)
    
    for pair, data in results.items():
        oi = data.get("binance")
        print(f"  {pair}: OI={oi}")
    
    await collector.shutdown()

if __name__ == "__main__":
    asyncio.run(_test())