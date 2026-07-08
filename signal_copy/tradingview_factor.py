"""TradingView API factor for signal validation.

Uses Mathieu2301/TradingView-API (Node.js) to fetch real-time indicators
and provides a confluence score.
"""

import asyncio
import json
import os
from typing import Any, Dict, Optional
from utils.logger import logger

BRIDGE_PATH = os.path.join(os.path.dirname(__file__), "tv_bridge", "bridge.cjs")

class TradingViewFactor:
    """Fetch TradingView TA summary and compute confluence score."""

    def __init__(self, timeout: int = 30):
        self.timeout = timeout

    async def fetch(self, symbol: str, indicators: Optional[list] = None) -> Dict[str, Any]:
        """Call the Node.js bridge to get TradingView data for a symbol."""
        try:
            payload = json.dumps({"symbol": symbol})
            proc = await asyncio.create_subprocess_exec(
                "node", BRIDGE_PATH,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(payload.encode()),
                timeout=self.timeout
            )
            if proc.returncode != 0:
                return {}
            data = json.loads(stdout.decode())
            if "error" in data:
                return {}
            return data
        except asyncio.TimeoutError:
            logger.warning("[TV_FACTOR] Timeout after %ss for %s", self.timeout, symbol)
            return {}
        except Exception as exc:
            logger.warning("[TV_FACTOR] Fetch failed for %s: %s", symbol, exc)
            return {}

    def compute_confluence(self, data: Dict[str, Any], side: str) -> Dict[str, Any]:
        """Compute confluence score from TradingView TA summary."""
        score = 50.0
        details = []
        ta = data.get("_ta", {})

        if not isinstance(ta, dict) or ta.get("error"):
            return {"score": 30.0, "details": ["No TV data"], "rsi": None, "ema20": None, "ema50": None}

        # Timeframes to check with weights
        tfs = [("15m", "15", 3), ("1h", "60", 2), ("4h", "240", 2), ("1D", "1D", 1)]
        aligned_count = 0

        for tf_name, tf_key, weight in tfs:
            tf_data = ta.get(tf_key, {})
            if not isinstance(tf_data, dict):
                continue
            all_score = tf_data.get("All", 0)
            ma_score = tf_data.get("MA", 0)

            aligned = (all_score > 0 and side == "LONG") or (all_score < 0 and side == "SHORT")
            ma_aligned = (ma_score > 0 and side == "LONG") or (ma_score < 0 and side == "SHORT")

            if aligned and ma_aligned:
                score += 3 * weight
                details.append(f"TV {tf_name} bullish" if side == "LONG" else f"TV {tf_name} bearish")
                aligned_count += 1
            elif aligned:
                score += 1.5 * weight
                details.append(f"TV {tf_name} partial")
                aligned_count += 0.5
            elif not aligned and abs(all_score) > 0.5:
                score -= 2 * weight
                details.append(f"TV {tf_name} against {side}")

        if aligned_count >= 2:
            score += 5

        return {
            "score": max(0, min(100, score)),
            "details": details[:5],
            "rsi": None,
            "ema20": None,
            "ema50": None,
        }
