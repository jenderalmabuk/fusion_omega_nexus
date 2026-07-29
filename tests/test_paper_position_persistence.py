import asyncio
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch


class PaperPositionPersistenceTest(unittest.TestCase):
    def test_positions_survive_reload(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "open.json"
            with patch.dict(os.environ, {"FQ_PAPER_POSITION_STATE": str(state)}):
                import importlib
                import execution.paper_mainnet_trader as module
                module = importlib.reload(module)
                original = {"BTCUSDT": {"symbol": "BTCUSDT", "opened_at": datetime.now(timezone.utc)}}
                module._persist_positions(original)
                restored = module._load_positions()
                self.assertEqual(restored["BTCUSDT"]["symbol"], "BTCUSDT")
                self.assertIsInstance(restored["BTCUSDT"]["opened_at"], datetime)


class PaperEquityPersistenceTest(unittest.TestCase):
    def test_equity_survives_reload(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "equity.json"
            with patch.dict(os.environ, {"FQ_PAPER_EQUITY_STATE": str(state)}):
                import importlib
                import execution.paper_mainnet_trader as module
                module = importlib.reload(module)
                self.assertIsNone(module.load_equity())
                module.persist_equity(987.65)
                self.assertAlmostEqual(module.load_equity(), 987.65)


class PaperCostModelTest(unittest.TestCase):
    def test_adverse_slippage_both_sides(self):
        from execution.paper_mainnet_trader import PaperMainnetTrader
        with patch.dict(os.environ, {"PAPER_SLIPPAGE_PCT": "0.0005"}):
            self.assertAlmostEqual(PaperMainnetTrader._adverse_fill(100, "LONG", opening=True), 100.05)
            self.assertAlmostEqual(PaperMainnetTrader._adverse_fill(100, "LONG", opening=False), 99.95)
            self.assertAlmostEqual(PaperMainnetTrader._adverse_fill(100, "SHORT", opening=True), 99.95)
            self.assertAlmostEqual(PaperMainnetTrader._adverse_fill(100, "SHORT", opening=False), 100.05)

    def test_partial_exit_charges_exit_fee_and_slippage(self):
        from execution.paper_mainnet_trader import PaperMainnetTrader

        class Risk:
            current = 1000.0
            def get_current_equity(self):
                return self.current
            def sync_balance(self, value):
                self.current = value

        trader = PaperMainnetTrader()
        trader.risk_mgr = Risk()
        trader._notify_close = lambda payload: asyncio.sleep(0)
        pos = {"symbol": "TEST", "side": "LONG", "entry_price": 100.0,
               "opened_at": datetime.now(timezone.utc), "qty": 0.5}
        with patch.dict(os.environ, {"PAPER_SLIPPAGE_PCT": "0.0005", "PAPER_TAKER_FEE_PCT": "0.0005"}), \
             patch("execution.paper_mainnet_trader.persist_equity"):
            asyncio.run(trader._realize_partial(pos, 110.0, 0.5, "TP1_PARTIAL"))
        self.assertAlmostEqual(trader.risk_mgr.current, 1004.94501375)


class ShadowTrailingTest(unittest.TestCase):
    def test_shadow_trailing_never_changes_provider_stop_or_qty(self):
        from execution.paper_mainnet_trader import PaperMainnetTrader
        trader = PaperMainnetTrader()
        pos = {"symbol": "TEST", "side": "LONG", "entry_price": 100.0,
               "sl_price": 90.0, "qty": 1.0, "next_tp_index": 1,
               "tp1_hit": True, "adv_snapshot": {"atr_pct": 0.5}}
        with patch.dict(os.environ, {"SHADOW_TRAILING_ENABLED": "true",
                                    "SHADOW_TRAIL_MIN_PCT": "0.01",
                                    "PAPER_SLIPPAGE_PCT": "0.0005",
                                    "PAPER_TAKER_FEE_PCT": "0.0005"}):
            trader._update_shadow_trailing(pos, 110.0)
            trader._update_shadow_trailing(pos, 108.8)
        self.assertEqual(pos["sl_price"], 90.0)
        self.assertEqual(pos["qty"], 1.0)
        self.assertAlmostEqual(pos["shadow_stop"], 108.9)
        self.assertTrue(pos["shadow_exit_price"] > 0)

    def test_shadow_inactive_before_explicit_tp1_event(self):
        from execution.paper_mainnet_trader import PaperMainnetTrader
        trader = PaperMainnetTrader()
        pos = {"symbol": "TEST", "side": "SHORT", "entry_price": 100.0,
               "sl_price": 110.0, "qty": 1.0, "next_tp_index": 1,
               "tp1_hit": False}
        trader._update_shadow_trailing(pos, 90.0)
        self.assertNotIn("shadow_trailing_active", pos)

    def test_legacy_position_without_tp1_field_is_shadow_ineligible(self):
        from execution.paper_mainnet_trader import PaperMainnetTrader
        trader = PaperMainnetTrader()
        pos = {"symbol": "LEGACY", "side": "LONG", "entry_price": 100.0,
               "sl_price": 90.0, "qty": 1.0, "next_tp_index": 2}
        trader._update_shadow_trailing(pos, 120.0)
        self.assertNotIn("shadow_trailing_active", pos)

    def test_stale_conflict_shadow_only_never_closes_position(self):
        from execution.paper_mainnet_trader import PaperMainnetTrader
        trader = PaperMainnetTrader()
        pos = {"symbol": "STALE", "side": "LONG", "entry_price": 100.0,
               "sl_price": 90.0, "qty": 1.0, "tp1_hit": False,
               "opened_at": datetime.now(timezone.utc),
               "adv_snapshot": {"stale_conflict_categories": 3}}
        pos["opened_at"] = pos["opened_at"] - timedelta(hours=19)
        with patch.dict(os.environ, {"STALE_SHADOW_ENABLED": "true"}):
            self.assertTrue(trader._record_stale_conflict_shadow(pos, 95.0))
        self.assertNotIn("shadow_stale_closed", pos)
        self.assertTrue(pos["shadow_stale_candidate"])


if __name__ == "__main__":
    unittest.main()
