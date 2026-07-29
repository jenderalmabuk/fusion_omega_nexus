import asyncio
import os
import unittest
from unittest.mock import patch

from fusion_quantum.paper_runner import State
from gateway.order_intent import OrderIntent
from gateway.service import ExecutionGateway


class DummyTrader:
    def __init__(self, count):
        self.positions = {f"P{i}": {} for i in range(count)}

    async def submit_open(self, **kwargs):
        return {"ok": True}


class DummyRisk:
    def __init__(self, trader):
        self.trader = trader

    def _position_count(self):
        return len(self.trader.positions)

    def check_risk_limits(self, **kwargs):
        return {"can_trade": True}


class FusionQuantumCapacityTest(unittest.TestCase):
    def test_pending_limits_are_unlimited(self):
        state = State.__new__(State)
        state.data = {"setups": {str(i): {"status": "pending"} for i in range(1000)}}
        self.assertTrue(state.can_add_pending())

    def test_twentieth_running_position_blocks_fill(self):
        trader = DummyTrader(20)
        gateway = ExecutionGateway(trader, DummyRisk(trader))
        intent = OrderIntent.from_dict({
            "source": "TEST", "symbol": "BTCUSDT", "side": "LONG",
            "entry_price": 100.0, "sl_price": 99.0, "tps": [102.0],
            "notional": 10.0, "tag": "fusion_quantum_h4_15m",
        })
        with patch.dict(os.environ, {"FQ_MAX_RUNNING_POSITIONS": "20"}):
            result = asyncio.run(gateway.execute(intent))
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "max running positions reached (20)")


if __name__ == "__main__":
    unittest.main()
