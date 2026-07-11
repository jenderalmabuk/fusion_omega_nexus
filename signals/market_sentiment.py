# signals/market_sentiment.py
from utils.logger import logger

class InstitutionalSentimentEngine:
    def __init__(self):
        self.sentiment_label = "NEUTRAL"
        self.sentiment_score = 0.0  # Range: -100 (Extreme Fear) to +100 (Extreme Greed)

    def evaluate_market_sentiment(self, adv_batch: dict, btc_adv: dict) -> str:
        """
        Mengevaluasi sentimen pasar berdasarkan 3 Pilar Institusional:
        1. Market Breadth (Momentum Mayoritas Altcoin)
        2. Derivatives Heat (Global Funding Rate)
        3. Smart Money (BTC Top Trader Ratio)
        """
        if not adv_batch or not btc_adv:
            return self.sentiment_label

        score = 0.0
        altcoin_count = len(adv_batch)
        
        # --- PILAR 1: MARKET BREADTH (Tren Altcoin 24 Jam) ---
        # Menghitung berapa banyak koin yang naik lebih dari 2% dalam 24 jam terakhir
        bullish_alts = 0
        bearish_alts = 0
        global_funding_sum = 0.0

        for sym, adv in adv_batch.items():
            price_change_24h = adv.get("price_change_24h_pct", 0.0)
            if price_change_24h > 2.0:
                bullish_alts += 1
            elif price_change_24h < -2.0:
                bearish_alts += 1
            
            global_funding_sum += adv.get("funding_rate_pct", 0.0)

        # Rasio altcoin hijau vs merah
        if altcoin_count > 0:
            bullish_ratio = bullish_alts / altcoin_count
            bearish_ratio = bearish_alts / altcoin_count
            
            # Jika > 60% altcoin hijau, tambah poin Bullish
            if bullish_ratio > 0.60: score += 30
            elif bullish_ratio > 0.40: score += 10
            # Jika > 60% altcoin merah, kurangi poin (Bearish)
            if bearish_ratio > 0.60: score -= 30
            elif bearish_ratio > 0.40: score -= 10

        # --- PILAR 2: DERIVATIVES HEAT (Tingkat Keserakahan/Ketakutan) ---
        # Market Maker suka melikuidasi mayoritas. Jika Funding Rate terlalu tinggi = Bahaya Dump.
        avg_funding_rate = global_funding_sum / altcoin_count if altcoin_count > 0 else 0.0
        
        if avg_funding_rate > 0.03: # Ritel sangat FOMO Long
            score -= 20  # Institusi bersiap nge-SHORT
            logger.debug(f"⚠️ Global Funding Rate Tinggi ({avg_funding_rate:.4f}%) -> Potensi Long Squeeze")
        elif avg_funding_rate < -0.03: # Ritel ketakutan (Extreme Fear)
            score += 20  # Institusi bersiap memborong (LONG)
            logger.debug(f"🔥 Global Funding Rate Negatif ({avg_funding_rate:.4f}%) -> Potensi Short Squeeze")

        # --- PILAR 3: SMART MONEY (BTC Top Trader Long/Short Ratio) ---
        # Apa yang dilakukan Top Trader di Bybit pada Raja Kripto (BTC)?
        btc_top_long_ratio = btc_adv.get("top_trader_long_ratio", 50.0)
        
        if btc_top_long_ratio > 65.0: # Top trader sangat yakin LONG
            score += 30
        elif btc_top_long_ratio > 55.0:
            score += 10
        elif btc_top_long_ratio < 35.0: # Top trader menimbun SHORT
            score -= 30
        elif btc_top_long_ratio < 45.0:
            score -= 10

        # --- KEPUTUSAN FINAL SENTIMEN ---
        self.sentiment_score = max(-100.0, min(100.0, score)) # Batasi -100 sampai 100

        if self.sentiment_score >= 40:
            self.sentiment_label = "BULLISH"
        elif self.sentiment_score <= -40:
            self.sentiment_label = "BEARISH"
        elif -20 < self.sentiment_score < 20:
            self.sentiment_label = "SQUEEZE" # Sedang ragu-ragu / konsolidasi
        else:
            self.sentiment_label = "NEUTRAL"

        logger.info(f"🌍 MARKET SENTIMENT: {self.sentiment_label} (Score: {self.sentiment_score:.1f}) | Alt Bullish: {bullish_ratio*100:.0f}% | BTC Top Longs: {btc_top_long_ratio:.1f}%")
        
        return self.sentiment_label

# Inisialisasi global
institutional_sentiment = InstitutionalSentimentEngine()
