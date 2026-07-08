"""
Telegram signal-group listener.

Reads messages from configured signal groups/channels and forwards their text
to a callback (the orchestrator). Two backends:

1. Telethon (user account / API_ID + API_HASH): can read ANY group the user is
   a member of. Best for copying public/private signal groups. Preferred.
2. aiogram (bot account): only reads chats where the bot is added as admin.
   Fallback.

Configure via signal_copy/signal_copy_config.py.
"""

from __future__ import annotations

import asyncio
from typing import Awaitable, Callable, List, Optional

from utils.logger import logger

# message callback: (text, source_name, chat_id) -> awaitable
MessageCallback = Callable[[str, str, Optional[int]], Awaitable[None]]


class TelegramSignalListener:
    def __init__(
        self,
        on_message: MessageCallback,
        *,
        api_id: Optional[int] = None,
        api_hash: str = "",
        session_name: str = "signal_copy_session",
        bot_token: str = "",
        channels: Optional[List[int]] = None,
        channel_names: Optional[dict] = None,
    ):
        self.on_message = on_message
        self.api_id = api_id
        self.api_hash = api_hash or ""
        self.session_name = session_name
        self.bot_token = bot_token or ""
        self.channels = set(channels or [])
        self.channel_names = channel_names or {}
        self.running = False
        self._client = None  # telethon client
        self._bot = None     # aiogram bot
        self._dp = None

    def _name_for(self, chat_id, fallback: str = "") -> str:
        return self.channel_names.get(chat_id, fallback or str(chat_id))

    # ---- Telethon backend (user account, reads any joined group) ----
    async def _start_telethon(self) -> bool:
        try:
            from telethon import TelegramClient, events
        except Exception:
            logger.info("[TG_LISTENER] telethon not installed; skipping user-account backend")
            return False
        if not self.api_id or not self.api_hash:
            logger.info("[TG_LISTENER] no API_ID/API_HASH; skipping telethon backend")
            return False

        self._client = TelegramClient(self.session_name, self.api_id, self.api_hash)

        @self._client.on(events.NewMessage())
        async def _handler(event):
            try:
                chat_id = event.chat_id
                # If channels filter is set, honor it; else accept all.
                if self.channels and chat_id not in self.channels:
                    return
                text = event.raw_text or ""
                if not text.strip():
                    return
                # Capture an attached chart image (photos only, to avoid
                # downloading large docs/videos) for vision enrichment.
                image = None
                try:
                    if getattr(event.message, "photo", None):
                        image = await event.message.download_media(file=bytes)
                except Exception as exc:
                    logger.debug("[TG_LISTENER] image download failed: %s", exc)
                    image = None
                name = self._name_for(chat_id)
                await self.on_message(text, name, chat_id, image)
            except Exception as exc:
                logger.exception("[TG_LISTENER] telethon handler error: %s", exc)

        await self._client.start()
        logger.info("[TG_LISTENER] telethon started (channels=%s)",
                    self.channels or "ALL joined")
        self.running = True
        await self._client.run_until_disconnected()
        return True

    # ---- aiogram backend (bot account, channels where bot is admin) ----
    async def _start_aiogram(self) -> bool:
        try:
            from aiogram import Bot, Dispatcher
            from aiogram.types import Message
        except Exception:
            logger.error("[TG_LISTENER] aiogram not installed; no telegram backend available")
            return False
        if not self.bot_token:
            logger.error("[TG_LISTENER] no bot token and no telethon creds; listener disabled")
            return False

        self._bot = Bot(token=self.bot_token)
        self._dp = Dispatcher()

        async def _on_any(message):
            try:
                if self.channels and message.chat.id not in self.channels:
                    return
                text = (message.text or message.caption or "")
                if not text.strip():
                    return
                name = self._name_for(message.chat.id, message.chat.title or "")
                await self.on_message(text, name, message.chat.id)
            except Exception as exc:
                logger.exception("[TG_LISTENER] aiogram handler error: %s", exc)

        self._dp.channel_post()(_on_any)
        self._dp.message()(_on_any)

        logger.info("[TG_LISTENER] aiogram started (bot mode)")
        self.running = True
        await self._dp.start_polling(self._bot, allowed_updates=["message", "channel_post"])
        return True

    async def start(self) -> None:
        # Prefer telethon (reads any joined group); fall back to aiogram bot.
        if self.api_id and self.api_hash:
            try:
                ok = await self._start_telethon()
                if ok:
                    return
            except Exception as exc:
                logger.warning("[TG_LISTENER] telethon failed (%s); trying aiogram", exc)
        await self._start_aiogram()

    async def stop(self) -> None:
        self.running = False
        try:
            if self._client:
                await self._client.disconnect()
        except Exception:
            pass
        try:
            if self._dp:
                await self._dp.stop_polling()
            if self._bot:
                await self._bot.session.close()
        except Exception:
            pass
