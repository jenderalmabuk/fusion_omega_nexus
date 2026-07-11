# whales/cex_ws_manager.py - VERSI SEDERHANA (TIDAK LAGI MEMANGGIL ASYNC)
import asyncio
from utils.logger import logger

class CEXWebSocketManager:
    def __init__(self):
        self.running = False
        logger.info("🌐 CEX Whale Scanner initialized (SIMPLE MODE)")

    async def start(self):
        if self.running:
            return
        self.running = True
        logger.info("✅ CEX Whale WS started (lightweight mode)")

    # Method get_whale_score sudah tidak digunakan lagi.
    # Perhitungan whale score dilakukan langsung dari data adv di main.py

# Singleton
ws_manager = CEXWebSocketManager()