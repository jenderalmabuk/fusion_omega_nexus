"""Bybit closed-15m observer. No trading, state writes, or Telegram."""
from __future__ import annotations

import asyncio
import json
import inspect
import os
import time
from typing import Any, Callable

import aiohttp

WS_URL = os.getenv("BYBIT_WS_URL", "wss://stream.bybit.com/v5/public/linear")
BATCH = int(os.getenv("BYBIT_WS_BATCH", "200"))


class ClosedCandleDeduper:
    def __init__(self) -> None:
        self.seen: set[tuple[str, int]] = set()

    def accept(self, symbol: str, start: int) -> bool:
        key = (symbol, start)
        if key in self.seen:
            return False
        self.seen.add(key)
        if len(self.seen) > 5000:
            self.seen = set(sorted(self.seen, key=lambda x: x[1])[-2500:])
        return True


def parse_kline(msg: dict[str, Any]) -> dict[str, Any] | None:
    data = msg.get("data") or []
    if not data or data[0].get("confirm") is not True:
        return None
    topic = str(msg.get("topic", ""))
    symbol = topic.rsplit(".", 1)[-1]
    return {"symbol": symbol, "start": int(data[0]["start"]), "closed": True}


def batches(symbols: list[str]) -> list[list[str]]:
    return [symbols[i:i + BATCH] for i in range(0, len(symbols), BATCH)]


def reconnect_delay(attempt: int) -> int:
    return min(30, 2 ** attempt)


async def maybe_call(callback: Callable[[dict[str, Any]], Any] | None, event: dict[str, Any]) -> None:
    if callback is None:
        return
    result = callback(event)
    if inspect.isawaitable(result):
        await result


async def observe(symbols: list[str], duration: int = 300,
                  callback: Callable[[dict[str, Any]], Any] | None = None) -> None:
    dedupe = ClosedCandleDeduper()
    tasks: set[asyncio.Task[Any]] = set()
    deadline = time.monotonic() + duration
    attempt = 0
    async with aiohttp.ClientSession() as session:
        while time.monotonic() < deadline:
            try:
                async with session.ws_connect(WS_URL, heartbeat=20, autoping=True) as ws:
                    for batch in batches(symbols):
                        await ws.send_json({"op": "subscribe", "args": [f"kline.15.{s}" for s in batch]})
                    attempt = 0
                    while time.monotonic() < deadline:
                        msg = await ws.receive(timeout=30)
                        if msg.type != aiohttp.WSMsgType.TEXT:
                            if msg.type in {aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR}:
                                raise ConnectionError(f"websocket {msg.type.name.lower()}")
                            continue
                        payload = json.loads(msg.data)
                        if payload.get("op") in {"subscribe", "ping"} or payload.get("success") is False:
                            print(json.dumps({"observer_only": True, "control": payload}), flush=True)
                            continue
                        event = parse_kline(payload)
                        if event and dedupe.accept(event["symbol"], event["start"]):
                            event = {**event, "received_at": time.time()}
                            print(json.dumps({"observer_only": True, **event}), flush=True)
                            task = asyncio.create_task(maybe_call(callback, event))
                            tasks.add(task)
                            task.add_done_callback(tasks.discard)
            except (aiohttp.ClientError, asyncio.TimeoutError, ConnectionError) as exc:
                delay = reconnect_delay(attempt)
                attempt += 1
                print(json.dumps({"observer_only": True, "reconnect_in": delay, "error": type(exc).__name__}), flush=True)
                await asyncio.sleep(min(delay, max(0, deadline - time.monotonic())))
    if tasks:
        await asyncio.gather(*tasks)


if __name__ == "__main__":
    path = os.getenv("FQ_UNIVERSE_FILE", "runtime/revo/canonical_universe.txt")
    symbols = sorted(set(x.strip().upper() for x in open(path) if x.strip()))
    asyncio.run(observe(symbols, int(os.getenv("BYBIT_WS_DURATION", "300"))))
