import json
import tempfile
import unittest
from pathlib import Path

from auditable_core.core import Candle, Config, GateInput, Position, decide_entry, decide_exit, append_jsonl, replay_candidates


class GateTests(unittest.TestCase):
    def test_gate_denies_stale_data_with_reason(self):
        decision = decide_entry(GateInput(symbol="BTC/USDT:USDT", score=10, rsi=35, discount_pct=4.0, qvol_med48=500_000, atr_pct=2.0, flow="long", btc_mode="neutral", btc_coupling="coupled", data_age_sec=999), Config())
        self.assertFalse(decision.allow)
        self.assertIn("DENY_DATA_STALE", decision.reasons)

    def test_gate_allows_high_quality_pullback(self):
        decision = decide_entry(GateInput(symbol="ETH/USDT:USDT", score=9, rsi=38, discount_pct=3.7, qvol_med48=600_000, atr_pct=2.1, flow="long", btc_mode="neutral", btc_coupling="coupled", data_age_sec=30), Config())
        self.assertTrue(decision.allow)
        self.assertEqual("long", decision.side)
        self.assertIn("ALLOW_A_GRADE_PULLBACK", decision.reasons)

    def test_btc_hard_dump_blocks_only_coupled_pairs(self):
        coupled = GateInput(symbol="X/USDT:USDT", score=10, rsi=35, discount_pct=4, qvol_med48=999_000, atr_pct=2, flow="long", btc_mode="hard_dump", btc_coupling="coupled", data_age_sec=1)
        decoupled = GateInput(**{**coupled.__dict__, "btc_coupling": "decoupled_positive"})
        self.assertFalse(decide_entry(coupled, Config()).allow)
        self.assertTrue(decide_entry(decoupled, Config()).allow)


class ExitTests(unittest.TestCase):
    def test_exit_true_decay_only_after_weak_mfe_and_bad_mae(self):
        pos = Position(symbol="ETH/USDT:USDT", side="long", entry_price=100, age_min=80, mfe_pct=0.15, mae_pct=-1.2, partial_done=False, thesis_valid=True, net_pnl_pct=-0.4)
        decision = decide_exit(pos, Config())
        self.assertTrue(decision.exit)
        self.assertEqual("EXIT_TRUE_DECAY", decision.reason)

    def test_exit_does_not_kill_recovery_safe_position(self):
        pos = Position(symbol="ETH/USDT:USDT", side="long", entry_price=100, age_min=80, mfe_pct=1.4, mae_pct=-1.2, partial_done=False, thesis_valid=True, net_pnl_pct=-0.1)
        decision = decide_exit(pos, Config())
        self.assertFalse(decision.exit)
        self.assertEqual("HOLD_RECOVERY_SAFE", decision.reason)


class JournalReplayTests(unittest.TestCase):
    def test_append_jsonl_and_replay_candidates(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "journal.jsonl"
            append_jsonl(path, {"event": "candidate", "symbol": "ETH/USDT:USDT", "score": 9})
            row = json.loads(path.read_text().strip())
            self.assertEqual("candidate", row["event"])
            candles = [Candle(close=100, volume=7000, rsi=38, ema55=104, atr_pct=2.0)]
            out = list(replay_candidates("ETH/USDT:USDT", candles, flow="long", btc_mode="neutral"))
            self.assertEqual(1, len(out))
            self.assertTrue(out[0].allow)


if __name__ == "__main__":
    unittest.main()
