# whales/redis_db.py - v14.5 FINAL THREAD-SAFE
from __future__ import annotations

import asyncio
import hashlib
import json
import time
from datetime import datetime, UTC
from typing import Any, Dict, List, Optional

import redis

from config import REDIS_HOST, REDIS_PORT, REDIS_DB
from utils.logger import logger

r = redis.Redis(
    host=REDIS_HOST,
    port=REDIS_PORT,
    db=REDIS_DB,
    decode_responses=True,
)

_MAIN_LOOP: Optional[asyncio.AbstractEventLoop] = None

WHALE_DEDUP_TTL_SEC = 300
WHALE_RECORD_TTL_SEC = 14400
MIN_CEX_WHALE_PERCENT = 2.0
MIN_ONCHAIN_WHALE_PERCENT = 2.0


def set_main_event_loop(loop: Optional[asyncio.AbstractEventLoop]) -> None:
    global _MAIN_LOOP
    _MAIN_LOOP = loop
    if loop is not None:
        logger.info("Whale Redis DB main loop registered for thread-safe scheduling")


def _get_whale_hash(symbol: str, percent: float, source: str) -> str:
    bucket = int(time.time() // 300)
    unique_str = f"{str(symbol).upper()}:{percent:.2f}:{source}:{bucket}"
    return hashlib.md5(unique_str.encode()).hexdigest()


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, "", "None"):
            return default
        return float(value)
    except Exception:
        return default


def _schedule_coroutine_threadsafe(coro) -> bool:
    global _MAIN_LOOP

    if _MAIN_LOOP is None:
        logger.debug("Whale Redis DB: main loop not registered; async alert skipped")
        return False

    if _MAIN_LOOP.is_closed():
        logger.debug("Whale Redis DB: main loop already closed; async alert skipped")
        return False

    try:
        future = asyncio.run_coroutine_threadsafe(coro, _MAIN_LOOP)

        def _done_callback(fut):
            try:
                fut.result()
            except Exception as exc:
                logger.debug(f"Whale Redis DB scheduled coroutine failed: {exc}")

        future.add_done_callback(_done_callback)
        return True
    except Exception as exc:
        logger.debug(f"Whale Redis DB schedule failed: {exc}")
        return False


async def _send_whale_alert_async(
    symbol: str,
    direction: str,
    percent: float,
    source: str,
    usd_value: float = 0.0,
) -> None:
    try:
        from telegram_notifier import send_whale_alert
        await send_whale_alert(symbol, direction, percent, source, usd_value)
    except Exception as exc:
        logger.debug(f"Telegram whale alert failed: {exc}")


def _trigger_whale_alert(
    symbol: str,
    direction: str,
    percent: float,
    source: str,
    usd_value: float = 0.0,
) -> None:
    coro = _send_whale_alert_async(symbol, direction, percent, source, usd_value)

    try:
        running_loop = asyncio.get_running_loop()
        running_loop.create_task(coro)
        return
    except RuntimeError:
        pass
    except Exception as exc:
        logger.debug(f"Whale Redis DB local loop scheduling failed: {exc}")

    scheduled = _schedule_coroutine_threadsafe(coro)
    if not scheduled:
        logger.debug(
            "Whale alert skipped safely (no available event loop) "
            f"for {symbol} {direction} {percent:+.2f}%"
        )


def _set_json_with_ttl(key: str, payload: Dict[str, Any], ttl_sec: int) -> None:
    r.setex(key, ttl_sec, json.dumps(payload, separators=(",", ":")))


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def add_onchain_whale(
    symbol: str,
    contract: str,
    percent: float,
    whale_address: str,
    tx_hash: str,
) -> bool:
    try:
        symbol = str(symbol or "").upper().strip()
        contract = str(contract or "").strip()
        whale_address = str(whale_address or "").strip()
        tx_hash = str(tx_hash or "").strip()
        percent = _safe_float(percent)

        if not symbol:
            logger.debug("On-chain whale ignored: empty symbol")
            return False

        if percent < MIN_ONCHAIN_WHALE_PERCENT:
            logger.debug(
                f"On-chain whale percent too low: {percent:.2f}% < {MIN_ONCHAIN_WHALE_PERCENT:.2f}%"
            )
            return False

        whale_hash = _get_whale_hash(symbol, percent, "onchain")
        dedup_key = f"whale_dedup:{whale_hash}"
        if r.exists(dedup_key):
            logger.debug(f"Duplicate on-chain whale ignored: {symbol} {percent:.2f}%")
            return False

        r.setex(dedup_key, WHALE_DEDUP_TTL_SEC, "1")

        key = f"onchain_whale:{symbol}:{datetime.now().strftime('%Y%m%d%H%M%S')}"
        data = {
            "id": key,
            "symbol": symbol,
            "contract": contract,
            "percent": percent,
            "whale_address": whale_address,
            "tx_hash": tx_hash,
            "detected_at": _now_iso(),
            "status": "pending",
            "source": "onchain",
        }

        _set_json_with_ttl(key, data, WHALE_RECORD_TTL_SEC)
        logger.info(f"🐋 On-chain Whale saved: {symbol} ({percent:.2f}%)")

        _trigger_whale_alert(symbol, "BUY", percent, "On-Chain", 0.0)
        return True

    except Exception as exc:
        logger.error(f"Redis save error (onchain whale): {exc}", exc_info=True)
        return False


def add_cex_whale(symbol: str, direction: str, percent: float, usd_value: float) -> bool:
    try:
        symbol = str(symbol or "").upper().strip()
        direction = str(direction or "BUY").upper().strip()
        percent = _safe_float(percent)
        usd_value = _safe_float(usd_value)

        if not symbol:
            logger.debug("CEX whale ignored: empty symbol")
            return False

        if percent < MIN_CEX_WHALE_PERCENT:
            logger.debug(
                f"Whale percent too low: {percent:.2f}% < {MIN_CEX_WHALE_PERCENT:.2f}%, ignored"
            )
            return False

        whale_hash = _get_whale_hash(symbol, percent, "cex")
        dedup_key = f"whale_dedup:{whale_hash}"
        if r.exists(dedup_key):
            logger.debug(f"Duplicate whale ignored: {symbol} {percent:.2f}%")
            return False

        r.setex(dedup_key, WHALE_DEDUP_TTL_SEC, "1")

        key = f"cex_whale:{symbol}:{datetime.now().strftime('%Y%m%d%H%M%S')}"
        data = {
            "id": key,
            "symbol": symbol,
            "direction": direction,
            "percent": percent,
            "usd": usd_value,
            "detected_at": _now_iso(),
            "status": "pending",
            "source": "telegram",
        }

        _set_json_with_ttl(key, data, WHALE_RECORD_TTL_SEC)
        logger.info(f"🐋 CEX Whale saved: {symbol} {direction} {percent:.2f}% (${usd_value:,.0f})")

        _trigger_whale_alert(symbol, direction, percent, "Telegram", usd_value)
        return True

    except Exception as exc:
        logger.error(f"Redis save error (cex whale): {exc}", exc_info=True)
        return False


def get_pending_whales() -> List[Dict[str, Any]]:
    try:
        whales: List[Dict[str, Any]] = []

        for key in r.scan_iter("onchain_whale:*"):
            data = r.get(key)
            if not data:
                continue
            try:
                whale = json.loads(data)
                if whale.get("status") == "pending":
                    whale.setdefault("id", key)
                    whales.append(whale)
            except Exception as exc:
                logger.debug(f"Invalid onchain whale payload at {key}: {exc}")

        for key in r.scan_iter("cex_whale:*"):
            data = r.get(key)
            if not data:
                continue
            try:
                whale = json.loads(data)
                if whale.get("status") == "pending":
                    whale.setdefault("id", key)
                    whales.append(whale)
            except Exception as exc:
                logger.debug(f"Invalid cex whale payload at {key}: {exc}")

        whales.sort(key=lambda item: item.get("detected_at", ""))
        return whales

    except Exception as exc:
        logger.error(f"Redis scan error: {exc}", exc_info=True)
        return []

def get_whale_pressure(symbol: str, window_minutes: int = 60) -> Dict[str, float]:
    """Calculate buy vs sell pressure from whales for a specific symbol."""
    all_whales = get_pending_whales()
    now = time.time()
    relevant = [
        w for w in all_whales 
        if w.get('symbol') == symbol and (now - w.get('timestamp', 0)) < (window_minutes * 60)
    ]
    
    buy_vol = sum(float(w.get('usd_value', 0)) for w in relevant if w.get('direction') == 'BUY')
    sell_vol = sum(float(w.get('usd_value', 0)) for w in relevant if w.get('direction') == 'SELL')
    
    return {
        'buy_vol': buy_vol,
        'sell_vol': sell_vol,
        'net_vol': buy_vol - sell_vol,
        'count': len(relevant)
    }


def mark_whale_processed(whale_id: str) -> bool:
    try:
        if not whale_id:
            return False

        data = r.get(whale_id)
        if not data:
            return False

        try:
            whale = json.loads(data)
        except Exception:
            r.delete(whale_id)
            return True

        whale["status"] = "processed"
        whale["processed_at"] = _now_iso()

        ttl = r.ttl(whale_id)
        ttl = ttl if ttl and ttl > 0 else 300
        _set_json_with_ttl(whale_id, whale, ttl)
        return True

    except Exception as exc:
        logger.error(f"Redis mark processed error: {exc}", exc_info=True)
        return False


def delete_whale(whale_id: str) -> bool:
    try:
        if not whale_id:
            return False
        r.delete(whale_id)
        return True
    except Exception as exc:
        logger.error(f"Redis delete error: {exc}", exc_info=True)
        return False
