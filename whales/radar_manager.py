# whales/radar_manager.py - EARLY SESSION AKTIF (3 SESI)
import asyncio
import time
from datetime import datetime
from config import EARLY_SESSION_ENABLED, EARLY_SESSIONS, EARLY_SURGE_MIN_PCT, EARLY_SURGE_VOLUME_RATIO, EARLY_CVD_BOOST_THRESHOLD, EARLY_SESSION_BOOST
from whales.redis_db import get_pending_whales
from utils.logger import logger

class RadarManager:
    def __init__(self):
        self.watchlist = {}
        self.last_early_session_check = 0

    def is_in_early_session(self):
        """Cek apakah sekarang sedang dalam early session"""
        if not EARLY_SESSION_ENABLED:
            return False, None

        now = datetime.now()
        current_hour = now.hour
        current_minute = now.minute

        for session in EARLY_SESSIONS:
            start_hour = session["start_hour"]
            duration = session["duration_min"]

            if current_hour == start_hour and current_minute < duration:
                return True, session["name"]

        return False, None

    async def start_radar(self):
        logger.info("📡 Whale Radar 24/7 + Early Session Momentum AKTIF (3 sesi)")

        while True:
            # Cek Early Session
            in_session, session_name = self.is_in_early_session()
            if in_session:
                logger.info(f"🔥 EARLY SESSION AKTIF → {session_name} (boost +{EARLY_SESSION_BOOST} poin)")

            # Cek pending whale dari Redis
            pending = get_pending_whales()
            for whale in pending:
                symbol = whale["symbol"]
                if symbol not in self.watchlist:
                    self.watchlist[symbol] = time.time()
                    logger.info(f"📡 Whale Radar activated for {symbol}")

            await asyncio.sleep(30)


radar = RadarManager()