import tempfile
import unittest
from pathlib import Path

from auditable_bot.backtest import replay_frames
from auditable_bot.config import BotConfig
from auditable_bot.models import MarketFrame


class HistoricalReplayTests(unittest.TestCase):
    def test_replay_frames_tracks_equity_and_trade_metrics(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = BotConfig(journal_dir=Path(tmp), true_decay_min_age=10)
            frames = [
                (0, [MarketFrame("ETH/USDT:USDT", 100, 3000, 39, 104, 2, 0.4, 0.2, -0.1, "long", "neutral", "coupled", 10)]),
                (15 * 60_000, [MarketFrame("ETH/USDT:USDT", 98.5, 3000, 45, 104, 2, 0.0, 0.0, 0.0, "neutral", "neutral", "coupled", 10)]),
            ]
            summary = replay_frames(frames, cfg)
            self.assertEqual(1, summary["entries"])
            self.assertEqual(1, summary["exits"])
            self.assertEqual(1, summary["trades"])
            self.assertLess(summary["realized_pnl_usdt"], 0)
            self.assertGreaterEqual(summary["max_drawdown_usdt"], 0)

    def test_replay_forced_closes_open_positions_and_reports_pf(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = BotConfig(journal_dir=Path(tmp), true_decay_min_age=999)
            frames = [
                (0, [MarketFrame("ETH/USDT:USDT", 100, 3000, 39, 104, 2, 0.4, 0.2, -0.1, "long", "neutral", "coupled", 10)]),
                (5 * 60_000, [MarketFrame("ETH/USDT:USDT", 103, 3000, 45, 104, 2, 0.0, 0.0, 0.0, "neutral", "neutral", "coupled", 10)]),
            ]
            summary = replay_frames(frames, cfg, force_close=True)
            self.assertEqual(1, summary["trades"])
            self.assertEqual(1, summary["forced_exits"])
            self.assertEqual(1, summary["wins"])
            self.assertEqual(0, summary["losses"])
            self.assertGreater(summary["profit_factor"], 1)
            self.assertGreater(summary["expectancy_usdt"], 0)


if __name__ == "__main__":
    unittest.main()
