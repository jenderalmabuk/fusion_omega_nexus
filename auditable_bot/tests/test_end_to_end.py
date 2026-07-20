import json
import tempfile
import unittest
from pathlib import Path

from auditable_bot.config import BotConfig
from auditable_bot.engine import AuditableBot
from auditable_bot.models import MarketFrame


class AuditableBotEndToEndTests(unittest.TestCase):
    def test_candidate_gate_entry_exit_and_journal(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = BotConfig(journal_dir=Path(tmp), max_open_positions=2, stake_usdt=100)
            bot = AuditableBot(cfg)
            frames = [
                MarketFrame("ETH/USDT:USDT", 100, 3000, 39, 104, 2.0, 0.4, 0.2, -0.1, "long", "neutral", "coupled", 10),
                MarketFrame("BAD/USDT:USDT", 100, 10, 60, 101, 9.0, -2.0, 0.0, 1.2, "hostile", "hard_dump", "coupled", 10),
            ]
            result = bot.run_cycle(frames, now_ms=0)
            self.assertEqual(1, result.entries)
            self.assertEqual(1, result.rejected)
            self.assertIn("ETH/USDT:USDT", bot.positions)
            bot.mark_price("ETH/USDT:USDT", 98.5)
            result = bot.run_cycle([], now_ms=90 * 60_000)
            self.assertEqual(1, result.exits)
            self.assertNotIn("ETH/USDT:USDT", bot.positions)
            gate_rows = [json.loads(x) for x in (Path(tmp) / "gate_decision.jsonl").read_text().splitlines()]
            exit_rows = [json.loads(x) for x in (Path(tmp) / "exit_decision.jsonl").read_text().splitlines()]
            self.assertTrue(any(r["allow"] for r in gate_rows))
            self.assertTrue(any("DENY_" in ",".join(r["reasons"]) for r in gate_rows))
            self.assertEqual("EXIT_TRUE_DECAY", exit_rows[-1]["reason"])

    def test_risk_blocks_duplicate_and_daily_loss(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = BotConfig(journal_dir=Path(tmp), max_open_positions=1, daily_loss_limit_usdt=1)
            bot = AuditableBot(cfg)
            good = MarketFrame("ETH/USDT:USDT", 100, 3000, 39, 104, 2.0, 0.4, 0.2, -0.1, "long", "neutral", "coupled", 10)
            bot.run_cycle([good], now_ms=0)
            second = bot.run_cycle([MarketFrame("SOL/USDT:USDT", 100, 3000, 39, 104, 2.0, 0.4, 0.2, -0.1, "long", "neutral", "coupled", 10)], now_ms=60_000)
            self.assertEqual(1, second.rejected)
            bot.realized_pnl_usdt = -2
            bot.positions.clear()
            blocked = bot.run_cycle([good], now_ms=120_000)
            self.assertEqual(1, blocked.rejected)


if __name__ == "__main__":
    unittest.main()
