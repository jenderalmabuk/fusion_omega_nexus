# whales/binance_ws_manager.py - final bounded websocket manager
from __future__ import annotations

import asyncio
import json
from collections import deque
from typing import Dict, Optional

import redis
import websockets

from config import MAX_CONCURRENT_REQUESTS, REDIS_DB, REDIS_HOST, REDIS_PORT, USE_WEBSOCKET_OI_CVD
from utils.logger import logger


class BinanceWebSocketManager:
    def __init__(self):
        self.base_url = "wss://fstream.binance.com/stream?streams="
        self.oi_history_15m: Dict[str, deque] = {}
        self.oi_history_1h: Dict[str, deque] = {}
        self.funding_cache: Dict[str, float] = {}
        self.running = False
        self.tasks: Dict[str, asyncio.Task] = {}
        self.connection_semaphore = asyncio.Semaphore(max(1, MAX_CONCURRENT_REQUESTS))

        self.redis: Optional[redis.Redis] = None
        try:
            self.redis = redis.Redis(
                host=REDIS_HOST,
                port=REDIS_PORT,
                db=REDIS_DB,
                decode_responses=True,
            )
            self.redis.ping()
            logger.info("BinanceWSManager: Redis connected")
        except Exception as exc:
            logger.warning("BinanceWSManager: Redis unavailable (%s)", exc)

        self._load_all_oi_history()

    def _load_all_oi_history(self):
        if not self.redis:
            return

        try:
            for key in self.redis.scan_iter("binance_ws:oi_history_15m:*"):
                symbol = key.split(":")[-1]
                data = self.redis.get(key)
                if data:
                    self.oi_history_15m[symbol] = deque(json.loads(data), maxlen=15)

            for key in self.redis.scan_iter("binance_ws:oi_history_1h:*"):
                symbol = key.split(":")[-1]
                data = self.redis.get(key)
                if data:
                    self.oi_history_1h[symbol] = deque(json.loads(data), maxlen=60)
        except Exception as exc:
            logger.warning("BinanceWSManager: failed to load OI history: %s", exc)

    def _save_oi_history(self, symbol: str):
        if not self.redis:
            return

        try:
            hist_15m = self.oi_history_15m.get(symbol)
            hist_1h = self.oi_history_1h.get(symbol)
            if hist_15m is not None:
                self.redis.set(f"binance_ws:oi_history_15m:{symbol}", json.dumps(list(hist_15m)))
            if hist_1h is not None:
                self.redis.set(f"binance_ws:oi_history_1h:{symbol}", json.dumps(list(hist_1h)))
        except Exception as exc:
            logger.debug("BinanceWSManager: failed to save OI history for %s: %s", symbol, exc)

    async def start(self):
        if not USE_WEBSOCKET_OI_CVD or self.running:
            return

        self.running = True
        symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "SUIUSDT", "DOGEUSDT"]
        logger.info("BinanceWSManager: starting %s websocket task(s)", len(symbols))

        for symbol in symbols:
            if symbol in self.tasks and not self.tasks[symbol].done():
                continue
            self.tasks[symbol] = asyncio.create_task(
                self.watch_symbol(symbol),
                name=f"binance_ws_{symbol.lower()}",
            )

    async def stop(self):
        self.running = False
        tasks = [task for task in self.tasks.values() if not task.done()]
        for task in tasks:
            task.cancel()

        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for result in results:
                if isinstance(result, Exception) and not isinstance(result, asyncio.CancelledError):
                    logger.debug("BinanceWSManager stop ignored result: %s", result)

        self.tasks.clear()

    async def watch_symbol(self, symbol: str):
        streams = f"{symbol.lower()}@openInterest/{symbol.lower()}@markPrice"
        stream_url = f"{self.base_url}{streams}"

        while self.running:
            websocket = None
            try:
                async with self.connection_semaphore:
                    websocket = await websockets.connect(
                        stream_url,
                        ping_interval=20,
                        ping_timeout=20,
                        close_timeout=5,
                        max_queue=100,
                    )
                    logger.info("BinanceWSManager: websocket connected for %s", symbol)

                    while self.running:
                        try:
                            raw_message = await asyncio.wait_for(websocket.recv(), timeout=35.0)
                        except asyncio.TimeoutError:
                            await websocket.ping()
                            continue

                        payload = json.loads(raw_message)
                        stream = payload.get("stream", "")
                        inner = payload.get("data", {})

                        if "@openInterest" in stream:
                            oi = float(inner.get("o", 0.0))
                            self._update_oi_history(symbol, oi)
                            self._save_oi_history(symbol)
                        elif "@markPrice" in stream:
                            self.funding_cache[symbol] = float(inner.get("r", inner.get("f", 0.0)))

            except asyncio.CancelledError:
                break
            except Exception as exc:
                if self.running:
                    logger.warning("BinanceWSManager: ws error for %s: %s", symbol, exc)
                    await asyncio.sleep(2.0)
            finally:
                if websocket is not None:
                    try:
                        await websocket.close()
                    except Exception:
                        pass

        logger.info("BinanceWSManager: websocket stopped for %s", symbol)

    def _update_oi_history(self, symbol: str, oi: float):
        if symbol not in self.oi_history_15m:
            self.oi_history_15m[symbol] = deque(maxlen=15)
        if symbol not in self.oi_history_1h:
            self.oi_history_1h[symbol] = deque(maxlen=60)

        self.oi_history_15m[symbol].append(float(oi))
        self.oi_history_1h[symbol].append(float(oi))

    def get_real_time_oi(self, symbol: str) -> float:
        hist = self.oi_history_1h.get(symbol)
        return float(hist[-1]) if hist else 0.0

    def get_oi_change_15m_pct(self, symbol: str) -> float:
        hist = self.oi_history_15m.get(symbol)
        if not hist or len(hist) < 2 or hist[0] == 0:
            return 0.0
        return round((hist[-1] - hist[0]) / hist[0] * 100.0, 2)

    def get_oi_change_1h_pct(self, symbol: str) -> float:
        hist = self.oi_history_1h.get(symbol)
        if not hist or len(hist) < 2 or hist[0] == 0:
            return 0.0
        return round((hist[-1] - hist[0]) / hist[0] * 100.0, 2)

    def get_funding_rate(self, symbol: str) -> float:
        return float(self.funding_cache.get(symbol, 0.0))

    def get_real_time_cvd(self, symbol: str) -> float:
        return 0.0


binance_ws = BinanceWebSocketManager()
