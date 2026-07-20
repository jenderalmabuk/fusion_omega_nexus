import json
import tempfile
import unittest
from pathlib import Path

from auditable_bot.datasource import freqtrade_pair_to_file_stem, market_frame_from_row
from auditable_bot.models import Position
from auditable_bot.report import summarize_journal
from auditable_bot.state import StateStore
from auditable_bot.universe import load_pair_whitelist


class StateUniverseReportTests(unittest.TestCase):
    def test_universe_loads_freqtrade_pair_whitelist(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "config.json"
            cfg.write_text(json.dumps({"exchange": {"pair_whitelist": ["ETH/USDT:USDT", "BTC/USDT:USDT"]}}))
            self.assertEqual(["ETH/USDT:USDT", "BTC/USDT:USDT"], load_pair_whitelist(cfg))

    def test_universe_loads_revo_runtime_pairs(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "freqtrade_pairlist.json"
            cfg.write_text(json.dumps({"pairs": ["BSB/USDT:USDT", "EVAA/USDT:USDT"]}))
            self.assertEqual(["BSB/USDT:USDT", "EVAA/USDT:USDT"], load_pair_whitelist(cfg))

    def test_datasource_maps_freqtrade_pair_and_row(self):
        self.assertEqual("ETH_USDT_USDT", freqtrade_pair_to_file_stem("ETH/USDT:USDT"))
        frame = market_frame_from_row("ETH/USDT:USDT", {"close": "100", "volume": "3000", "rsi": "39", "ema55": "104"}, 12)
        self.assertEqual(12, frame.data_age_sec)
        self.assertGreater(frame.ema55, frame.close)

    def test_state_roundtrip_positions_and_realized_pnl(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = StateStore(Path(tmp) / "state.json")
            pos = Position("ETH/USDT:USDT", "long", 100, 1.5, 0, 101, 99)
            store.save({"ETH/USDT:USDT": pos}, -3.25)
            positions, pnl = store.load()
            self.assertEqual(-3.25, pnl)
            self.assertEqual(100, positions["ETH/USDT:USDT"].entry_price)
            self.assertEqual(99, positions["ETH/USDT:USDT"].min_price)

    def test_report_summarizes_gate_and_trade_journal(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "gate_decision.jsonl").write_text(
                '{"allow":true,"reasons":["ALLOW_A_GRADE_PULLBACK"]}\n'
                '{"allow":false,"reasons":["DENY_SCORE","DENY_RSI"]}\n'
            )
            (root / "paper_trades.jsonl").write_text(
                '{"event":"entry","symbol":"ETH/USDT:USDT"}\n'
                '{"event":"exit","symbol":"ETH/USDT:USDT","pnl_usdt":1.2}\n'
            )
            summary = summarize_journal(root)
            self.assertEqual(2, summary["gate_decisions"])
            self.assertEqual(1, summary["entries"])
            self.assertEqual(1.2, summary["realized_pnl_usdt"])
            self.assertEqual(1, summary["reasons"]["DENY_SCORE"])


if __name__ == "__main__":
    unittest.main()
