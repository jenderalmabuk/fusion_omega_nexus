# signals/oi_cvd_micro_tracker.py
"""
OI & CVD Micro-Timeframe Tracker (1M & 5M)
Untuk konfirmasi Order Block entry dengan institutional flow
"""

from collections import deque
from typing import Dict, Any, Tuple
from utils.logger import logger


class OICVDMicroTracker:
    """
    Track OI (Open Interest) dan CVD (Cumulative Volume Delta) 
    pada timeframe 1M dan 5M untuk konfirmasi smart money flow
    """
    
    def __init__(self, history_1m: int = 60, history_5m: int = 60):
        """
        Args:
            history_1m: Jumlah data point 1M yang disimpan (default 60 = 1 hour)
            history_5m: Jumlah data point 5M yang disimpan (default 60 = 5 hours)
        """
        self.oi_1m: Dict[str, deque] = {}
        self.oi_5m: Dict[str, deque] = {}
        self.cvd_1m: Dict[str, deque] = {}
        self.cvd_5m: Dict[str, deque] = {}
        
        self.history_1m = history_1m
        self.history_5m = history_5m
    
    def update_oi_1m(self, symbol: str, oi_value: float):
        """Update OI 1M data"""
        if symbol not in self.oi_1m:
            self.oi_1m[symbol] = deque(maxlen=self.history_1m)
        self.oi_1m[symbol].append(float(oi_value))
    
    def update_oi_5m(self, symbol: str, oi_value: float):
        """Update OI 5M data"""
        if symbol not in self.oi_5m:
            self.oi_5m[symbol] = deque(maxlen=self.history_5m)
        self.oi_5m[symbol].append(float(oi_value))
    
    def update_cvd_1m(self, symbol: str, cvd_value: float):
        """Update CVD 1M data"""
        if symbol not in self.cvd_1m:
            self.cvd_1m[symbol] = deque(maxlen=self.history_1m)
        self.cvd_1m[symbol].append(float(cvd_value))
    
    def update_cvd_5m(self, symbol: str, cvd_value: float):
        """Update CVD 5M data"""
        if symbol not in self.cvd_5m:
            self.cvd_5m[symbol] = deque(maxlen=self.history_5m)
        self.cvd_5m[symbol].append(float(cvd_value))
    
    def get_oi_surge_1m(self, symbol: str, lookback: int = 5) -> Tuple[bool, float]:
        """
        Cek apakah ada peningkatan OI di 1M timeframe
        
        Returns:
            (is_surging, change_pct)
        """
        if symbol not in self.oi_1m or len(self.oi_1m[symbol]) < lookback + 1:
            return False, 0.0
        
        hist = list(self.oi_1m[symbol])
        recent_avg = sum(hist[-lookback:]) / lookback
        prev_avg = sum(hist[-(lookback * 2):-lookback]) / lookback if len(hist) >= lookback * 2 else hist[0]
        
        if prev_avg <= 0:
            return False, 0.0
        
        change_pct = ((recent_avg - prev_avg) / prev_avg) * 100.0
        is_surging = change_pct > 0.5  # 0.5% increase threshold
        
        return is_surging, round(change_pct, 4)
    
    def get_oi_surge_5m(self, symbol: str, lookback: int = 3) -> Tuple[bool, float]:
        """
        Cek apakah ada peningkatan OI di 5M timeframe
        """
        if symbol not in self.oi_5m or len(self.oi_5m[symbol]) < lookback + 1:
            return False, 0.0
        
        hist = list(self.oi_5m[symbol])
        recent_avg = sum(hist[-lookback:]) / lookback
        prev_avg = sum(hist[-(lookback * 2):-lookback]) / lookback if len(hist) >= lookback * 2 else hist[0]
        
        if prev_avg <= 0:
            return False, 0.0
        
        change_pct = ((recent_avg - prev_avg) / prev_avg) * 100.0
        is_surging = change_pct > 0.3  # 0.3% increase threshold for 5M
        
        return is_surging, round(change_pct, 4)
    
    def get_cvd_surge_1m(self, symbol: str, lookback: int = 5) -> Tuple[bool, float]:
        """
        Cek apakah ada peningkatan CVD di 1M timeframe
        
        Returns:
            (is_positive_flow, delta_sum)
        """
        if symbol not in self.cvd_1m or len(self.cvd_1m[symbol]) < lookback:
            return False, 0.0
        
        hist = list(self.cvd_1m[symbol])
        recent_sum = sum(hist[-lookback:])
        
        is_positive = recent_sum > 0
        
        return is_positive, round(recent_sum, 2)
    
    def get_cvd_surge_5m(self, symbol: str, lookback: int = 3) -> Tuple[bool, float]:
        """
        Cek apakah ada peningkatan CVD di 5M timeframe
        """
        if symbol not in self.cvd_5m or len(self.cvd_5m[symbol]) < lookback:
            return False, 0.0
        
        hist = list(self.cvd_5m[symbol])
        recent_sum = sum(hist[-lookback:])
        
        is_positive = recent_sum > 0
        
        return is_positive, round(recent_sum, 2)
    
    def check_ob_touch_confirmation(
        self,
        symbol: str,
        direction: str = "LONG",
        oi_1m_threshold: float = 0.5,
        oi_5m_threshold: float = 0.3,
        cvd_positive_required: bool = True
    ) -> Dict[str, Any]:
        """
        Cek konfirmasi OI & CVD saat price menyentuh Order Block
        
        Strategy:
        1. OI harus meningkat (accumulation signal)
        2. CVD harus positif untuk LONG (buying pressure)
        3. Kedua timeframe (1M & 5M) harus confirm
        
        Args:
            symbol: Trading symbol
            direction: "LONG" atau "SHORT"
            oi_1m_threshold: Minimum OI change % untuk 1M
            oi_5m_threshold: Minimum OI change % untuk 5M
            cvd_positive_required: Require positive CVD for confirmation
        
        Returns:
            {
                'oi_1m_confirmed': bool,
                'oi_5m_confirmed': bool,
                'cvd_1m_confirmed': bool,
                'cvd_5m_confirmed': bool,
                'full_confirmation': bool,
                'oi_1m_change_pct': float,
                'oi_5m_change_pct': float,
                'cvd_1m_delta': float,
                'cvd_5m_delta': float,
                'confidence_score': float (0-100)
            }
        """
        # Get OI surges
        oi_1m_surge, oi_1m_pct = self.get_oi_surge_1m(symbol)
        oi_5m_surge, oi_5m_pct = self.get_oi_surge_5m(symbol)
        
        # Get CVD flows
        cvd_1m_positive, cvd_1m_delta = self.get_cvd_surge_1m(symbol)
        cvd_5m_positive, cvd_5m_delta = self.get_cvd_surge_5m(symbol)
        
        # Direction-specific logic
        if direction == "LONG":
            oi_1m_ok = oi_1m_surge and oi_1m_pct >= oi_1m_threshold
            oi_5m_ok = oi_5m_surge and oi_5m_pct >= oi_5m_threshold
            cvd_1m_ok = cvd_1m_positive if cvd_positive_required else True
            cvd_5m_ok = cvd_5m_positive if cvd_positive_required else True
        else:  # SHORT
            oi_1m_ok = oi_1m_surge and oi_1m_pct >= oi_1m_threshold
            oi_5m_ok = oi_5m_surge and oi_5m_pct >= oi_5m_threshold
            cvd_1m_ok = not cvd_1m_positive if cvd_positive_required else True
            cvd_5m_ok = not cvd_5m_positive if cvd_positive_required else True
        
        # Compute confidence score
        confidence = 0.0
        if oi_1m_ok:
            confidence += 25.0
        if oi_5m_ok:
            confidence += 25.0
        if cvd_1m_ok:
            confidence += 25.0
        if cvd_5m_ok:
            confidence += 25.0
        
        full_confirmation = oi_1m_ok and oi_5m_ok and cvd_1m_ok and cvd_5m_ok
        
        result = {
            'oi_1m_confirmed': oi_1m_ok,
            'oi_5m_confirmed': oi_5m_ok,
            'cvd_1m_confirmed': cvd_1m_ok,
            'cvd_5m_confirmed': cvd_5m_ok,
            'full_confirmation': full_confirmation,
            'oi_1m_change_pct': oi_1m_pct,
            'oi_5m_change_pct': oi_5m_pct,
            'cvd_1m_delta': cvd_1m_delta,
            'cvd_5m_delta': cvd_5m_delta,
            'confidence_score': confidence
        }
        
        if full_confirmation:
            logger.info(
                "[OB_OI_CVD_CONFIRM] %s %s | oi_1m=%.2f%% | oi_5m=%.2f%% | cvd_1m=%.2f | cvd_5m=%.2f | score=%.1f | FULL_CONFIRM",
                symbol,
                direction,
                oi_1m_pct,
                oi_5m_pct,
                cvd_1m_delta,
                cvd_5m_delta,
                confidence
            )
        
        return result
    
    def get_statistics(self, symbol: str) -> Dict[str, Any]:
        """Get current statistics for a symbol"""
        return {
            'oi_1m_samples': len(self.oi_1m.get(symbol, [])),
            'oi_5m_samples': len(self.oi_5m.get(symbol, [])),
            'cvd_1m_samples': len(self.cvd_1m.get(symbol, [])),
            'cvd_5m_samples': len(self.cvd_5m.get(symbol, [])),
        }


# Global instance
_micro_tracker = OICVDMicroTracker()


def get_micro_tracker() -> OICVDMicroTracker:
    """Get the global micro tracker instance"""
    return _micro_tracker
