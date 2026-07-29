import asyncio
import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pandas as pd

from execution.paper_mainnet_trader import PaperMainnetTrader
from fusion_quantum.paper_runner import pending_action


class FillIntegrityTest(unittest.TestCase):
    def test_pre_order_wick_cannot_fill_new_limit(self):
        setup = {"side": "LONG", "entry_price": 100.0, "expires_at": "2026-07-26T12:00:00Z"}
        bars = pd.DataFrame({
            "open_time": pd.to_datetime(["2026-07-26T10:00:00Z", "2026-07-26T10:01:00Z"]),
            "low": [99.0, 101.0], "high": [102.0, 103.0],
        })
        self.assertEqual(
            pending_action(setup, bars, pd.Timestamp("2026-07-26T10:00:30Z"), pd.Timestamp("2026-07-26T10:02:00Z")),
            "wait",
        )

    def test_wall_clock_expiry_wins_over_old_candle_timestamp(self):
        setup = {"side": "SHORT", "entry_price": 100.0, "expires_at": "2026-07-26T10:00:00"}
        bars = pd.DataFrame({
            "open_time": pd.to_datetime(["2026-07-26T09:59:00Z"]),
            "low": [98.0], "high": [101.0],
        })
        self.assertEqual(
            pending_action(setup, bars, pd.Timestamp("2026-07-26T09:00:00Z"), pd.Timestamp("2026-07-26T10:00:01Z")),
            "expired",
        )

    def test_gateway_rejects_limit_when_mark_already_beyond_stop(self):
        async def run():
            trader = PaperMainnetTrader()
            trader._get_mark_price = AsyncMock(return_value=98.0)
            with patch("execution.paper_mainnet_trader._persist_positions"):
                got = await trader.submit_open(
                    symbol="TESTUSDT", side="LONG", entry_price=100.0,
                    sl=99.0, tp1=102.0, notional=100.0,
                    tag="fusion_quantum_pending_h4_15m",
                )
            self.assertIsNone(got)
            self.assertNotIn("TESTUSDT", trader.positions)
        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
