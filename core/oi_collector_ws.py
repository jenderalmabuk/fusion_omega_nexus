"""
fusion_omega_nexus/core/oi_collector_ws.py
WebSocket-based OI collector — Binance recommends WS for live updates.
Combined stream: up to 200 symbols per WebSocket connection.
No rate limits, real-time push.

Architecture:
- 3 WebSocket connections for 504 pairs (~168 per conn)
- Each symbol pushes OI every 1 second
- Background collector maintains latest OI snapshot
"""

import asyncio
import websockets
import json
import time
from typing import Dict, List, Optional, Tuple, Set
from collections import deque


class OICollectorWS:
    """
    WebSocket-based OI collector for Binance Futures.
    
    Uses combined streams:
    wss://fapi.binance.com/stream?streams=BTCUSDT@openInterest/ETHUSDT@openInterest/...
    
    Max 200 streams per connection (Binance limit).
    For 504 pairs: 3 connections (168 + 168 + 168).
    """
    
    # Binance WebSocket limits
    MAX_STREAMS_PER_CONN = 200
    WS_BASE = "wss://fapi.binance.com/stream"
    RECONNECT_DELAY = 5  # seconds
    
    def __init__(self, pairs: List[str] = None, history_depth: int = 60):
        self.pairs = [p.replace("-USDT-SWAP", "").replace("USDTUSDT", "USDT") 
                      for p in (pairs or [])]
        
        # OI snapshot: {symbol: (oi_value, timestamp)}
        self.oi_snapshot: Dict[str, Tuple[float, float]] = {}
        
        # History: {symbol: deque of (ts, oi)}
        self.history: Dict[str, deque] = {}
        self.history_depth = history_depth
        
        # Connection management
        self._tasks: List[asyncio.Task] = []
        self._running = False
        self._lock = asyncio.Lock()
    
    def _build_streams(self) -> List[List[str]]:
        """Split pairs into groups of MAX_STREAMS_PER_CONN."""
        streams = []
        for i in range(0, len(self.pairs), self.MAX_STREAMS_PER_CONN):
            chunk = self.pairs[i:i + self.MAX_STREAMS_PER_CONN]
            # Format: symbol@openInterest
            stream_names = [f"{s.lower()}@openinterest" for s in chunk]
            streams.append(stream_names)
        return streams
    
    async def _ws_connection(self, stream_names: List[str], conn_id: int):
        """Single WebSocket connection handler with reconnection."""
        # Build combined stream URL
        streams_param = "/".join(stream_names)
        url = f"{self.WS_BASE}?streams={streams_param}"
        
        symbol_map = {s.split("@")[0].upper(): s.split("@")[0].upper() 
                      for s in stream_names}
        
        print(f"[oi_ws:{conn_id}] Connecting to {len(stream_names)} symbols...")
        
        while self._running:
            try:
                async with websockets.connect(url, ping_interval=20) as ws:
                    print(f"[oi_ws:{conn_id}] Connected ({len(stream_names)} symbols)")
                    
                    async for msg in ws:
                        if not self._running:
                            break
                        
                        try:
                            data = json.loads(msg)
                            stream = data.get("stream", "")
                            symbol = stream.split("@")[0].upper()
                            oi_val = float(data.get("data", {}).get("openInterest", 0))
                            
                            async with self._lock:
                                self.oi_snapshot[symbol] = (oi_val, time.time())
                                
                                # Update history
                                if symbol not in self.history:
                                    self.history[symbol] = deque(maxlen=self.history_depth)
                                self.history[symbol].append((time.time(), oi_val))
                        except Exception:
                            pass
                            
            except websockets.ConnectionClosed:
                print(f"[oi_ws:{conn_id}] Connection closed, reconnecting in {self.RECONNECT_DELAY}s...")
            except Exception as e:
                print(f"[oi_ws:{conn_id}] Error: {e}, reconnecting in {self.RECONNECT_DELAY}s...")
            
            if self._running:
                await asyncio.sleep(self.RECONNECT_DELAY)
    
    async def start(self):
        """Start all WebSocket connections."""
        self._running = True
        
        stream_groups = self._build_streams()
        print(f"[oi_ws] Starting {len(stream_groups)} connections for {len(self.pairs)} pairs")
        
        for i, group in enumerate(stream_groups):
            task = asyncio.create_task(self._ws_connection(group, i))
            self._tasks.append(task)
        
        # Wait for first data to arrive
        await asyncio.sleep(15)
        
        count = len(self.oi_snapshot)
        print(f"[oi_ws] Initial snapshot: {count}/{len(self.pairs)} symbols received")
    
    async def stop(self):
        """Stop all connections."""
        self._running = False
        
        for task in self._tasks:
            task.cancel()
        
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        
        print(f"[oi_ws] Stopped. Final snapshot: {len(self.oi_snapshot)} symbols")
    
    def get_oi(self, symbol: str) -> float:
        """Get latest OI value for a symbol."""
        entry = self.oi_snapshot.get(symbol)
        if entry:
            return entry[0]
        return 0.0
    
    def get_oi_delta(self, symbol: str, lookback: int = 16) -> float:
        """OI change percentage over lookback samples."""
        hist = self.history.get(symbol)
        if not hist or len(hist) < lookback:
            return 0.0
        
        oi_list = [h[1] for h in list(hist)]
        now = oi_list[-1]
        past = oi_list[-lookback] if len(oi_list) >= lookback else oi_list[0]
        
        if past <= 0:
            return 0.0
        
        return ((now - past) / past) * 100
    
    def available_symbols(self) -> Set[str]:
        """Symbols with OI data."""
        return set(self.oi_snapshot.keys())
    
    def snapshot_age(self, symbol: str) -> float:
        """Age of OI data for a symbol in seconds."""
        entry = self.oi_snapshot.get(symbol)
        if entry:
            return time.time() - entry[1]
        return float('inf')


# ---------------------------------------------------------------------------
# CLI test
# ---------------------------------------------------------------------------

async def _test():
    print("=== OI WebSocket Test ===")
    test_pairs = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "DOGEUSDT"]
    
    collector = OICollectorWS(pairs=test_pairs)
    
    try:
        await collector.start()
        
        # Let it run for 30 seconds
        for i in range(6):
            await asyncio.sleep(5)
            
            snapshot = collector.oi_snapshot
            print(f"\n--- t={i*5+15}s ---")
            for pair in test_pairs:
                oi = collector.get_oi(pair)
                age = collector.snapshot_age(pair)
                delta = collector.get_oi_delta(pair, lookback=5)
                if oi > 0:
                    print(f"  {pair:10s}: OI={oi:>15,.0f} | age={age:.1f}s | delta_5={delta:+.2f}%")
                else:
                    print(f"  {pair:10s}: no data yet")
    finally:
        await collector.stop()

if __name__ == "__main__":
    asyncio.run(_test())