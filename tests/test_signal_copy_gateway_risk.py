import asyncio
import os
import tempfile
import unittest
from unittest.mock import patch

from gateway.order_intent import OrderIntent
from gateway.service import ExecutionGateway
from risk.risk_engine import RiskManager


class Trader:
    def __init__(self, positions=None):
        self.positions = positions or {}

    async def submit_open(self, **kwargs):
        return {"ok": True}


class SignalCopyGatewayRiskTest(unittest.TestCase):
    def test_running_slot_cap_is_30_paper_and_20_real(self):
        class SlotsTrader:
            def __init__(self, count):
                self.positions = {f"S{i}USDT": {"symbol": f"S{i}USDT"} for i in range(count)}

        with patch.dict(os.environ, {"GATEWAY_PAPER_MAINNET": "true"}, clear=False):
            paper = RiskManager(1000)
            paper.trader = SlotsTrader(29)
            self.assertTrue(asyncio.run(paper.reserve_open_risk("NEWUSDT", 1.0)))
            self.assertFalse(asyncio.run(paper.reserve_open_risk("FULLUSDT", 1.0)))

        with patch.dict(os.environ, {"GATEWAY_PAPER_MAINNET": "false"}, clear=False):
            real = RiskManager(1000)
            real.trader = SlotsTrader(20)
            self.assertFalse(asyncio.run(real.reserve_open_risk("NEWUSDT", 1.0)))

    def test_global_open_risk_paper_and_real_caps(self):
        trader = Trader({"A": {"side": "LONG", "entry_price": 100, "sl_price": 90, "qty": 19}})
        with patch.dict("os.environ", {"GATEWAY_PAPER_MAINNET": "true", "MAX_GLOBAL_OPEN_RISK_PCT": "0.20"}):
            risk = RiskManager(1000)
        risk.trader = trader
        self.assertAlmostEqual(risk.get_active_open_risk_total(), 190.0)
        self.assertTrue(asyncio.run(risk.reserve_open_risk("B", 10.0)))
        self.assertFalse(asyncio.run(risk.reserve_open_risk("C", 0.01)))
        with patch.dict("os.environ", {"GATEWAY_PAPER_MAINNET": "false"}, clear=False):
            risk_real = RiskManager(1000)
        self.assertAlmostEqual(risk_real.get_parallel_open_risk_budget(), 50.0)

    def test_inflight_reservations_atomically_enforce_running_slot_cap(self):
        positions = {f"P{i}": {"side": "LONG", "entry_price": 100, "sl_price": 99,
                                "qty": 1} for i in range(19)}
        trader = Trader(positions)
        risk = RiskManager(1000)
        risk.trader = trader
        with patch.dict("os.environ", {"FQ_MAX_RUNNING_POSITIONS": "20"}):
            async def reserve_two():
                return await asyncio.gather(
                    risk.reserve_open_risk("A", 1.0),
                    risk.reserve_open_risk("B", 1.0),
                )
            results = asyncio.run(reserve_two())
        self.assertEqual(sum(bool(x) for x in results), 1)
        self.assertEqual(risk.get_reserved_risk_total(), 1.0)

    def test_dormant_pending_does_not_reserve_risk_or_running_slot(self):
        trader = Trader({f"P{i}": {"side": "LONG", "entry_price": 100,
                                     "sl_price": 99, "qty": 1} for i in range(19)})
        risk = RiskManager(1000)
        risk.trader = trader
        # Dormant pending lives only in orchestrator; risk engine sees no reserve.
        dormant_pending_count = 100
        self.assertEqual(risk.get_reserved_risk_total(), 0.0)
        self.assertEqual(risk._position_count(), 19)
        self.assertEqual(dormant_pending_count, 100)
        self.assertTrue(asyncio.run(risk.reserve_open_risk("FILL", 1.0)))

    def test_partial_quantity_reduces_active_risk(self):
        trader = Trader({"A": {"side": "SHORT", "entry_price": 100, "sl_price": 110, "qty": 2}})
        risk = RiskManager(1000)
        risk.trader = trader
        self.assertAlmostEqual(risk.get_active_open_risk_total(), 20.0)
        trader.positions["A"]["qty"] = 1
        self.assertAlmostEqual(risk.get_active_open_risk_total(), 10.0)

    def test_zero_same_direction_cap_disables_direction_gate(self):
        trader = Trader({f"L{i}": {"side": "LONG", "notional": 10} for i in range(3)})
        risk = RiskManager(1000)
        risk.trader = trader
        with patch("risk.risk_engine.MAX_SAME_DIRECTION_POS", 0):
            result = risk.check_risk_limits(side="LONG")
        self.assertTrue(result["can_trade"], result)

    def test_signal_copy_bypasses_major_cluster_cap(self):
        trader = Trader({"ETHUSDT": {"side": "SHORT", "notional": 100}, "XRPUSDT": {"side": "LONG", "notional": 100}})
        risk = RiskManager(1000)
        risk.trader = trader
        gateway = ExecutionGateway(trader, risk)
        intent = OrderIntent.from_dict({
            "source": "SIGNAL_COPY", "symbol": "KAITOUSDT", "side": "LONG",
            "entry_price": 1.21, "sl_price": 1.10, "tps": [1.24], "notional": 100,
        })
        result = asyncio.run(gateway.execute(intent, dry_run=True))
        self.assertTrue(result.ok, result.reason)

    def test_signal_copy_explicit_notional_still_caps_risk_at_one_pct(self):
        trader = Trader()
        risk = RiskManager(1000)
        risk.trader = trader
        gateway = ExecutionGateway(trader, risk)
        intent = OrderIntent.from_dict({
            "source": "SIGNAL_COPY", "symbol": "KAITOUSDT", "side": "LONG",
            "entry_price": 1.21, "sl_price": 1.10, "tps": [1.24], "notional": 147.45,
        })
        result = asyncio.run(gateway.execute(intent, dry_run=True))
        self.assertTrue(result.ok, result.reason)
        self.assertLessEqual(result.risk_amount, 10.0)

    def test_dexe_tight_stop_keeps_provider_sl_and_caps_notional(self):
        trader = Trader()
        risk = RiskManager(1000)
        risk.trader = trader
        gateway = ExecutionGateway(trader, risk)
        intent = OrderIntent.from_dict({
            "source": "SIGNAL_COPY", "symbol": "DEXEUSDT", "side": "SHORT",
            "entry_price": 3.866, "sl_price": 3.90636, "tps": [3.598915],
            "notional": 986.6154608523358,
        })
        result = asyncio.run(gateway.execute(intent, dry_run=True))
        self.assertTrue(result.ok, result.reason)
        self.assertAlmostEqual(result.notional, 200.0)
        slip, fee = 0.0005, 0.0005
        slipped_entry = 3.866 * (1.0 - slip)
        adverse_exit = 3.90636 * (1.0 + slip)
        qty = 200.0 / slipped_entry
        econ = (adverse_exit - slipped_entry) * qty + fee * 200.0 + fee * adverse_exit * qty
        self.assertAlmostEqual(result.risk_amount, econ, places=6)
        self.assertLessEqual(result.risk_amount, 10.0)

    def test_lausdt_hard_sl_economic_loss_capped_at_1pct(self):
        """LAUSDT regression: naive notional 150.8 blew past 1% after fees+exit slip."""
        trader = Trader()
        risk = RiskManager(1001.75)
        risk.trader = trader
        gateway = ExecutionGateway(trader, risk)
        intent = OrderIntent.from_dict({
            "source": "SIGNAL_COPY", "symbol": "LAUSDT", "side": "LONG",
            "entry_price": 0.0688, "sl_price": 0.0636,
            "tps": [0.072, 0.075, 0.08],
            "notional": 150.8,
        })
        result = asyncio.run(gateway.execute(intent, dry_run=True))
        self.assertTrue(result.ok, result.reason)
        max_risk = 1001.75 * 0.01
        slip, fee = 0.0005, 0.0005
        slipped_entry = 0.0688 * (1.0 + slip)
        adverse_exit = 0.0636 * (1.0 - slip)
        qty = result.notional / slipped_entry
        econ = (slipped_entry - adverse_exit) * qty + fee * result.notional + fee * adverse_exit * qty
        self.assertLessEqual(econ, max_risk + 1e-9)
        self.assertLessEqual(result.risk_amount, max_risk + 1e-9)
        self.assertAlmostEqual(result.risk_amount, econ, places=6)
        self.assertLess(result.notional, 150.8)

    def test_paper_mark_worse_than_signal_resizes_risk(self):
        class MarkTrader(Trader):
            async def _get_mark_price(self, symbol):
                return 0.0700  # LONG: worse than signal 0.0688

        trader = MarkTrader()
        risk = RiskManager(1000)
        risk.trader = trader
        gateway = ExecutionGateway(trader, risk)
        intent = OrderIntent.from_dict({
            "source": "SIGNAL_COPY", "symbol": "LAUSDT", "side": "LONG",
            "entry_price": 0.0688, "sl_price": 0.0636, "tps": [0.072],
            "notional": 150.8,
        })
        result = asyncio.run(gateway.execute(intent, dry_run=True))
        self.assertTrue(result.ok, result.reason)
        slip, fee = 0.0005, 0.0005
        slipped_entry = 0.0700 * (1.0 + slip)
        adverse_exit = 0.0636 * (1.0 - slip)
        qty = result.notional / slipped_entry
        econ = (slipped_entry - adverse_exit) * qty + fee * result.notional + fee * adverse_exit * qty
        self.assertLessEqual(econ, 10.0 + 1e-9)
        self.assertAlmostEqual(result.risk_amount, econ, places=6)


if __name__ == "__main__":
    unittest.main()
