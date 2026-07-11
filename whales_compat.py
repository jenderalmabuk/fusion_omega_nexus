"""
Compatibility shim for whales/ modules from fusion_xomegabot.

Provides the old bot's `config` module and `telegram_notifier` module
using nexus environment variables (.env / docker env).
"""
from __future__ import annotations

import os
import sys
from typing import Optional

# Load .env if available
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass


# =============================================================================
# OLD BOT `config` MODULE COMPATIBILITY
# =============================================================================

# Telegram
TELEGRAM_WHALE_PARSER_BOT_TOKEN = os.getenv("SIGNAL_COPY_TG_LISTENER_BOT_TOKEN", "").strip() or os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_WHALE_CHANNELS = [
    int(x.strip()) for x in os.getenv("SIGNAL_COPY_TG_CHANNELS", "").replace(";", ",").split(",") if x.strip()
]

# Redis
REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
REDIS_DB = int(os.getenv("REDIS_DB", "0"))

# On-chain monitor
ONCHAIN_THRESHOLD_PERCENT = float(os.getenv("ONCHAIN_THRESHOLD_PERCENT", "2.0"))

# Telegram notifier (for whale alerts)
TELEGRAM_NOTIFIER_BOT_TOKEN = os.getenv("SIGNAL_COPY_NOTIFY_BOT_TOKEN", "").strip() or os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_NOTIFIER_CHAT_ID = os.getenv("SIGNAL_COPY_SIGNALS_CHAT_ID", "").strip() or os.getenv("TELEGRAM_CHAT_ID", "").strip()

# Legacy (for backward compat)
TELEGRAMBOTTOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
BINANCE_TESTNET_API_KEY = os.getenv("BINANCE_TESTNET_API_KEY", "dummy")
BINANCE_TESTNET_API_SECRET = os.getenv("BINANCE_TESTNET_API_SECRET", "dummy")
NINE_ROUTER_API_KEY = os.getenv("NINE_ROUTER_API_KEY", "dummy")


# =============================================================================
# OLD BOT `telegram_notifier` MODULE COMPATIBILITY
# =============================================================================

import asyncio
import logging
from typing import Any, Dict, List

import aiohttp

logger = logging.getLogger("fusion_nexus.telegram_notifier")


async def send_telegram(
    message: str,
    wait_delivery: bool = False,
    delivery_timeout: float = 10.0,
) -> bool:
    """Send a message to Telegram using the notifier bot token."""
    token = TELEGRAM_NOTIFIER_BOT_TOKEN
    chat_id = TELEGRAM_NOTIFIER_CHAT_ID

    if not token or not chat_id:
        logger.debug("Telegram notifier not configured (token/chat_id missing), skipping send")
        return False

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True,
    }

    try:
        timeout = aiohttp.ClientTimeout(total=delivery_timeout)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, json=payload) as resp:
                if resp.status == 200:
                    logger.debug("Telegram notifier: message sent OK")
                    return True
                else:
                    text = await resp.text()
                    logger.warning(f"Telegram notifier send failed: {resp.status} {text}")
                    return False
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.warning(f"Telegram notifier exception: {exc}")
        return False


async def send_whale_alert(
    symbol: str,
    direction: str,
    percent: float,
    source: str,
    usd_value: float = 0.0,
) -> bool:
    """Send formatted whale alert to Telegram."""
    direction_emoji = "🟢" if direction.upper() == "BUY" else "🔴"
    source_emoji = "🔗" if source.lower() == "onchain" else "📱"

    lines = [
        f"{direction_emoji} **WHALE ALERT** {source_emoji}",
        f"Symbol: **{symbol}USDT**",
        f"Direction: **{direction.upper()}**",
        f"Amount: **{percent:.2f}%** of supply",
        f"Source: {source}",
    ]
    if usd_value > 0:
        lines.append(f"USD Value: **${usd_value:,.0f}**")

    message = "\n".join(lines)
    return await send_telegram(message)


# =============================================================================
# INSTALL COMPATIBILITY MODULES INTO sys.modules
# =============================================================================

# Create a fake config module
import types
config_module = types.ModuleType("config")
for name in dir():
    if not name.startswith("_"):
        setattr(config_module, name, globals()[name])
sys.modules["config"] = config_module

# Create a fake telegram_notifier module
notifier_module = types.ModuleType("telegram_notifier")
notifier_module.send_telegram = send_telegram
notifier_module.send_whale_alert = send_whale_alert
sys.modules["telegram_notifier"] = notifier_module

# Ensure utils.logger is available
from utils.logger import logger as _logger  # noqa: F401
utils_logger_module = types.ModuleType("utils.logger")
utils_logger_module.logger = _logger
sys.modules["utils.logger"] = utils_logger_module

logger.info("✅ whales_compat loaded: config + telegram_notifier + utils.logger shims installed")


# Convenience: expose for direct import
__all__ = [
    "TELEGRAM_WHALE_PARSER_BOT_TOKEN",
    "TELEGRAM_WHALE_CHANNELS",
    "REDIS_HOST",
    "REDIS_PORT",
    "REDIS_DB",
    "ONCHAIN_THRESHOLD_PERCENT",
    "TELEGRAM_NOTIFIER_BOT_TOKEN",
    "TELEGRAM_NOTIFIER_CHAT_ID",
    "send_telegram",
    "send_whale_alert",
]