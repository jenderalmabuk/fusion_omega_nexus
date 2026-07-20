#!/usr/bin/env python3

from __future__ import annotations

import asyncio
import os
from typing import Optional

from utils.logger import logger

try:
    from telegram import Bot
    from telegram.error import TelegramError
    BOT_AVAILABLE = True
except ImportError:  # pragma: no cover
    BOT_AVAILABLE = False
    logger.warning("⚠️ python-telegram-bot tidak terinstall. Install: pip install python-telegram-bot")

# Config from environment
PARSER_BOT_TOKEN = os.getenv("SIGNAL_COPY_PARSER_NOTIFY_BOT_TOKEN", "")
PARSER_CHAT_ID = int(os.getenv("SIGNAL_COPY_PARSER_NOTIFY_CHAT_ID", "0"))
TRADES_BOT_TOKEN = os.getenv("SIGNAL_COPY_TRADES_NOTIFY_BOT_TOKEN", "")
TRADES_CHAT_ID = int(os.getenv("SIGNAL_COPY_TRADES_NOTIFY_CHAT_ID", "0"))

_parser_bot: Optional[Bot] = None
_trades_bot: Optional[Bot] = None


async def _ensure_bot_ready():
    global _parser_bot, _trades_bot
    if _parser_bot is None and PARSER_BOT_TOKEN:
        _parser_bot = Bot(PARSER_BOT_TOKEN)
        await _parser_bot.initialize()
        me = await _parser_bot.get_me()
        logger.info(f"✅ Parser bot ready: @{me.username} (id={me.id})")
    if _trades_bot is None and TRADES_BOT_TOKEN:
        try:
            _trades_bot = Bot(TRADES_BOT_TOKEN)
            await _trades_bot.initialize()
            me = await _trades_bot.get_me()
            logger.info(f"✅ Trades bot ready: @{me.username} (id={me.id})")
        except TelegramError:
            logger.warning("⚠️ Trades bot token invalid, using parser bot fallback")
            _trades_bot = None


async def send_parser_notification(message: str, chart_path: Optional[str] = None) -> bool:
    """Send validation report via parser bot."""
    if not BOT_AVAILABLE or not PARSER_BOT_TOKEN:
        logger.warning("⚠️ Parser bot not configured")
        return False
    await _ensure_bot_ready()
    try:
        if chart_path:
            try:
                from telegram import InputFile
                with open(chart_path, "rb") as fh:
                    await _parser_bot.send_photo(
                        chat_id=PARSER_CHAT_ID,
                        photo=InputFile(fh, filename=chart_path.rsplit("/", 1)[-1]),
                        caption=message[:1024],
                        parse_mode="HTML",
                    )
                    return True
            except Exception as exc:
                logger.warning(f"⚠️ Parser chart send failed, fallback to text: {exc}")
        await _parser_bot.send_message(
            chat_id=PARSER_CHAT_ID,
            text=message,
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
        return True
    except Exception as exc:
        logger.error(f"❌ Parser bot send failed: {exc}")
        return False


async def send_trades_notification(message: str) -> bool:
    """Send trades execution message via trades bot, or fallback to parser bot."""
    if not BOT_AVAILABLE:
        logger.warning("⚠️ Telethon/bots not available")
        return False
    await _ensure_bot_ready()

    target_bot = _trades_bot or _parser_bot
    if not target_bot:
        logger.warning("⚠️ No bot available")
        return False

    wrapped = "🔄 [TRADES] " + message
    try:
        await target_bot.send_message(
            chat_id=TRADES_CHAT_ID if target_bot is _trades_bot else PARSER_CHAT_ID,
            text=wrapped,
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
        return True
    except Exception as exc:
        logger.error(f"❌ Trades bot send failed: {exc}")
        return False


# Async dummies to maintain compat
async def start_telegram_workers():
    """Start the drivers (noop when using direct Bot(token))."""
    await _ensure_bot_ready()  # Ensure bots ready on start


async def stop_telegram_workers():
    """Graceful shutdown (noop for this driver)."""
    pass


__all__ = ["send_parser_notification", "send_trades_notification", "start_telegram_workers", "stop_telegram_workers"]