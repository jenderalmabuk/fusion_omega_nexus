# whales/telegram_whale_parser.py - v14.5 ULTIMATE (6 FORMAT SUPPORT)
# Supports: Whale Hunter (CoinTrendz), Whale Alert Official, Whale Sniper,
#           WhaleBot Alerts, Lookonchain (Gemini), Legacy

from __future__ import annotations

import asyncio
import json
import re
from typing import Optional

from aiogram import Bot, Dispatcher
from aiogram.types import Message

from config import TELEGRAM_WHALE_PARSER_BOT_TOKEN, TELEGRAM_WHALE_CHANNELS
from utils.logger import logger
from whales.redis_db import add_cex_whale, set_main_event_loop

# ========== GOOGLE GEMINI SETUP ==========
try:
    import google.generativeai as genai
    GEMINI_AVAILABLE = True
except ImportError:
    GEMINI_AVAILABLE = False
    logger.warning("google-generativeai not installed. Run: pip install google-generativeai")

GEMINI_API_KEY = "AIzaSyAwwVkGCNDuBtLIMNSLrSk9_XL9z87i1yE"

# ========== PATTERN 1: Whale Hunter (CoinTrendz) - NEW ==========
# Contoh real: "**TUT/USDT [Binance]**\nBig Whales **Buy** Activity ✳\n...\n🚨Order Size: **20,074 USDT** (**5.33%**)"
# Note: Telegram sends bold as **text** in raw_text
WHALE_HUNTER_PATTERN = re.compile(
    r"\*{0,2}([A-Z0-9]+)/USDT\s*\[Binance\]\*{0,2}.*?"
    r"Big Whales\s+\*{0,2}(Buy|Sell)\*{0,2}\s+Activity.*?"
    r"Order Size:\s*\*{0,2}([\d.,]+)\s*USDT\*{0,2}",
    re.IGNORECASE | re.DOTALL
)

# ========== PATTERN 2: Whale Alert Official ==========
# Real format: "🚨 🚨 🚨  50,783 $ETH (103,089,192 USD) transferred from unknown wallet to #Coinbase"
WHALE_ALERT_PATTERN = re.compile(
    r"([\d.,]+)\s*\$?#?([A-Z0-9]+)\s*\(([\d.,]+)\s*USD\)\s*(?:transferred|minted|burned|swapped)\s+from\s+(.+?)\s+to\s+(.+?)(?:\n|\[|$)",
    re.IGNORECASE
)

# ========== PATTERN 3: Whale Sniper ==========
# Real format: "#GMT - Unusual activity\n2.65M USDT in **14 minutes** (11%)"
# Also: "#KAITO - Unusual **buying** activity\n290K USDT in **11 minutes** (11%)"
WHALE_SNIPER_PATTERN = re.compile(
    r"#([A-Z0-9]+)\s*-\s*(?:Unusual|Whale)\s+(?:\*{0,2}(buying|selling)\*{0,2}\s+)?activity.*?"
    r"([\d.,]+[KMB]?)\s*USDT\s+in\s+\*{0,2}[\d.]+\s*(?:seconds?|minutes?|hours?)\*{0,2}\s*\((\d+(?:\.\d+)?)%\)",
    re.IGNORECASE | re.DOTALL
)

# ========== PATTERN 4: WhaleBot Alerts ==========
# Real format: "🚨 133 BTC ($10,101,138) transfered from Bitfinex to Unknown"
# Also: "🔑 10,000,000 XRP ($13,369,469) unlocked from Unknown to Unknown"
WHALEBOT_ALERT_PATTERN = re.compile(
    r"([\d.,]+)\s*([A-Z0-9]+)\s*\(\$?([\d.,]+)\)\s*(?:transfered|transferred|unlocked|sent|moved)\s+from\s+([\w\s.#]+?)\s+to\s+([\w\s.#]+?)(?:\d|$|\n)",
    re.IGNORECASE
)

# ========== PATTERN 5: Legacy ==========
LEGACY_WHALE_PATTERN = re.compile(
    r"(?i)(whale|smart money|accumulation|large buy|big buy|added|loaded|bought|buying|sell|dump|distribution).*?([A-Z0-9]+(?:USDT|BTC)?).*?([+-]?\d{1,3}(?:\.\d+)?)%",
    re.IGNORECASE,
)


class TelegramWhaleParser:
    def __init__(self):
        self.bot: Optional[Bot] = None
        self.dp: Optional[Dispatcher] = None
        self.running = False
        self.main_loop: Optional[asyncio.AbstractEventLoop] = None

        # Inisialisasi Gemini
        self.gemini_model = None
        if GEMINI_AVAILABLE:
            try:
                genai.configure(api_key=GEMINI_API_KEY)
                self.gemini_model = genai.GenerativeModel('gemini-1.5-flash')
                logger.info("✅ Google Gemini initialized for Lookonchain parsing (FREE)")
            except Exception as e:
                logger.warning(f"Failed to init Gemini: {e}")

        logger.info("Telegram Whale Parser initialized (v14.5 ULTIMATE - 6 Formats)")

    def _normalize_symbol(self, raw_symbol: str) -> str:
        symbol = str(raw_symbol or "").strip().upper()
        if not symbol:
            return ""
        symbol = symbol.lstrip("#")
        if any(symbol.endswith(s) for s in ["USDT", "USDC", "BTC", "ETH", "BUSD", "TUSD", "DAI"]):
            return symbol
        crypto_symbols = {"BTC", "ETH", "XRP", "SOL", "BNB", "ADA", "DOGE", "MATIC", "DOT", "LINK", "TRUMP", "HYPE", "ETHFI"}
        if symbol in crypto_symbols:
            return f"{symbol}USDT"
        # Default: tambahkan USDT
        return f"{symbol}USDT"

    def _infer_direction_from_entities(self, from_entity: str, to_entity: str) -> str:
        exchange_keywords = {"kraken", "binance", "coinbase", "bybit", "okx", "okex", "kucoin", "huobi", "gate", "gemini", "robinhood", "bitfinex"}
        from_lower = (from_entity or "").lower()
        to_lower = (to_entity or "").lower()

        if any(ex in to_lower for ex in exchange_keywords):
            return "SELL"
        if any(ex in from_lower for ex in exchange_keywords):
            return "BUY"
        return "NEUTRAL"

    def _infer_direction_from_text(self, text: str) -> str:
        text_lower = text.lower()
        buy_words = ("received", "withdrew", "bought", "accumulating", "withdraw", "buy", "long", "minted")
        sell_words = ("sold", "closed", "sell", "dump", "distribution", "burned")

        if any(word in text_lower for word in sell_words):
            return "SELL"
        if any(word in text_lower for word in buy_words):
            return "BUY"
        return "NEUTRAL"

    def _parse_number(self, num_str: str) -> float:
        try:
            return float(num_str.replace(",", ""))
        except Exception:
            return 0.0

    async def _parse_with_gemini(self, text: str) -> list[dict]:
        if not self.gemini_model:
            return []

        prompt = f"""
        Extract all whale/institutional transaction information from the following text.
        Return a JSON array of objects, each with these fields:
        - symbol: the asset symbol (e.g., "BTC", "ETH"). Always append "USDT" if not already present.
        - direction: "BUY" if accumulating/receiving/buying, "SELL" if selling/dumping, "NEUTRAL" if unclear.
        - usd_value: the USD value as a number (without $ or M/K suffixes).

        Text:
        {text[:2000]}

        Return ONLY a valid JSON array.
        """

        try:
            response = await asyncio.to_thread(
                self.gemini_model.generate_content,
                prompt,
                generation_config=genai.types.GenerationConfig(temperature=0, max_output_tokens=500)
            )
            content = response.text.strip()

            if content.startswith("```json"):
                content = content[7:]
            elif content.startswith("```"):
                content = content[3:]
            if content.endswith("```"):
                content = content[:-3]
            content = content.strip()

            events = json.loads(content)
            if isinstance(events, dict):
                events = [events]
            return events if isinstance(events, list) else []
        except Exception as e:
            logger.error(f"Gemini parsing failed: {e}")
            return []

    def _schedule_on_main_loop(self, coro) -> None:
        if self.main_loop is None or self.main_loop.is_closed():
            return
        try:
            future = asyncio.run_coroutine_threadsafe(coro, self.main_loop)
            future.add_done_callback(lambda f: f.result() if not f.exception() else None)
        except Exception:
            pass

    async def _handle_whale_detected(
        self, symbol: str, direction: str, usd_value: float, raw_text: str, source_chat_id: int
    ) -> None:
        try:
            if usd_value < 50000:
                logger.debug(f"Whale value too small: ${usd_value:,.0f}, ignored")
                return

            # Calculate percent based on USD value tiers
            # This represents the "significance" of the whale activity
            if usd_value >= 5_000_000:
                whale_percent = 10.0
            elif usd_value >= 2_000_000:
                whale_percent = 6.0
            elif usd_value >= 1_000_000:
                whale_percent = 4.0
            elif usd_value >= 500_000:
                whale_percent = 3.0
            elif usd_value >= 200_000:
                whale_percent = 2.5
            else:
                whale_percent = 2.0  # minimum to pass MIN_CEX_WHALE_PERCENT

            saved = add_cex_whale(symbol, direction, whale_percent, usd_value)
            if not saved:
                logger.debug(f"Whale event ignored/deduped: {symbol} {direction}")
                return

            logger.info(
                f"🐋 WHALE DETECTED -> {symbol} {direction} | value=${usd_value:,.0f} | "
                f"percent={whale_percent:.1f}% | chat={source_chat_id}"
            )
        except Exception as exc:
            logger.error(f"Failed to process whale event: {exc}", exc_info=True)

    async def _process_lookonchain(self, text: str, chat_id: int):
        if not self.gemini_model:
            return

        events = await self._parse_with_gemini(text)
        logger.info(f"Gemini extracted {len(events)} whale events from Lookonchain")

        for event in events:
            symbol = self._normalize_symbol(event.get("symbol", ""))
            if not symbol:
                continue
            usd_value = float(event.get("usd_value", 0))
            if usd_value > 0:
                await self._handle_whale_detected(
                    symbol=symbol,
                    direction=event.get("direction", "NEUTRAL"),
                    usd_value=usd_value,
                    raw_text=text[:200],
                    source_chat_id=chat_id,
                )

    def register_handlers(self):
        @self.dp.channel_post()
        async def handle_channel_post(message: Message):
            await self._process_whale_message(message)

        @self.dp.message()
        async def handle_message(message: Message):
            await self._process_whale_message(message)

    async def _process_whale_message(self, message: Message):
        try:
            if message.chat.id not in TELEGRAM_WHALE_CHANNELS:
                return

            text = (message.text or message.caption or "").strip()
            if not text:
                return

            # ========== DETEKSI LOOKONCHAIN (GEMINI) ==========
            if ("lookonchain" in text.lower() or "arkm.com" in text.lower()) and self.gemini_model:
                await self._process_lookonchain(text, message.chat.id)
                return

            # ========== FORMAT 1: WHALE HUNTER (CoinTrendz) ==========
            hunter_match = WHALE_HUNTER_PATTERN.search(text)
            if hunter_match:
                raw_symbol = hunter_match.group(1)
                activity_type = hunter_match.group(2).upper()
                usd_value_str = hunter_match.group(3)

                symbol = self._normalize_symbol(raw_symbol)
                if symbol:
                    direction = "BUY" if activity_type == "BUY" else "SELL"
                    usd_value = self._parse_number(usd_value_str)
                    await self._handle_whale_detected(
                        symbol, direction, usd_value, text, message.chat.id
                    )
                return

            # ========== FORMAT 2: WHALE ALERT OFFICIAL ==========
            whale_alert_match = WHALE_ALERT_PATTERN.search(text)
            if whale_alert_match:
                raw_symbol = whale_alert_match.group(2)
                usd_value_str = whale_alert_match.group(3)
                from_entity = whale_alert_match.group(4) or ""
                to_entity = whale_alert_match.group(5) or ""

                symbol = self._normalize_symbol(raw_symbol)
                if symbol:
                    usd_value = self._parse_number(usd_value_str)
                    direction = self._infer_direction_from_entities(from_entity, to_entity)
                    if direction == "NEUTRAL":
                        direction = self._infer_direction_from_text(text)
                    await self._handle_whale_detected(
                        symbol, direction, usd_value, text, message.chat.id
                    )
                return

            # ========== FORMAT 3: WHALE SNIPER ==========
            sniper_match = WHALE_SNIPER_PATTERN.search(text)
            if sniper_match:
                raw_symbol = sniper_match.group(1)
                activity_type = (sniper_match.group(2) or "").lower()
                volume_str = sniper_match.group(3)
                symbol = self._normalize_symbol(raw_symbol)
                if symbol:
                    if "buy" in activity_type:
                        direction = "BUY"
                    elif "sell" in activity_type:
                        direction = "SELL"
                    else:
                        direction = self._infer_direction_from_text(text)
                    usd_value = self._parse_number(volume_str.replace("M", "000000").replace("K", "000").replace("B", "000000000"))
                    await self._handle_whale_detected(
                        symbol, direction, usd_value, text, message.chat.id
                    )
                return

            # ========== FORMAT 4: WHALEBOT ALERTS ==========
            whalebot_match = WHALEBOT_ALERT_PATTERN.search(text)
            if whalebot_match:
                raw_symbol = whalebot_match.group(2)
                usd_value_str = whalebot_match.group(3)
                from_entity = whalebot_match.group(4)
                to_entity = whalebot_match.group(5)
                symbol = self._normalize_symbol(raw_symbol)
                if symbol:
                    usd_value = self._parse_number(usd_value_str)
                    direction = self._infer_direction_from_entities(from_entity, to_entity)
                    await self._handle_whale_detected(
                        symbol, direction, usd_value, text, message.chat.id
                    )
                return

            # ========== FORMAT 5: LEGACY ==========
            legacy_match = LEGACY_WHALE_PATTERN.search(text)
            if legacy_match:
                symbol = self._normalize_symbol(legacy_match.group(2))
                if symbol:
                    direction = self._infer_direction_from_text(text)
                    await self._handle_whale_detected(
                        symbol, direction, 0, text, message.chat.id
                    )

        except Exception as exc:
            logger.error(f"Telegram whale handler error: {exc}", exc_info=True)

    async def start_whale_parser(self):
        if self.running:
            return

        self.running = True
        self.main_loop = asyncio.get_running_loop()
        set_main_event_loop(self.main_loop)

        try:
            self.bot = Bot(token=TELEGRAM_WHALE_PARSER_BOT_TOKEN)
            self.dp = Dispatcher()
            self.register_handlers()

            formats = "Whale Hunter + Whale Alert + Sniper + WhaleBot + Legacy"
            if self.gemini_model:
                formats += " + Lookonchain (Gemini)"

            logger.info(f"🐋 Whale Parser AKTIF -> {len(TELEGRAM_WHALE_CHANNELS)} channels ({formats})")
            await self.dp.start_polling(self.bot, allowed_updates=["message", "channel_post"])
        except Exception as exc:
            logger.warning(f"Telegram Whale Parser gagal start: {exc}")
        finally:
            self.running = False

    async def stop(self):
        self.running = False
        try:
            if self.bot:
                await self.bot.session.close()
        except Exception:
            pass


whale_parser = TelegramWhaleParser()


async def start_whale_parser():
    await whale_parser.start_whale_parser()