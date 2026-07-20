import tempfile
import unittest
from pathlib import Path

from auditable_bot.config import BotConfig
from auditable_bot.replay import replay_csv


class ReplayTests(unittest.TestCase):
    def test_replay_csv_writes_summary_and_journal(self):
        with tempfile.TemporaryDirectory() as tmp:
            csv = Path(tmp) / "sample.csv"
            csv.write_text(
                "symbol,close,volume,rsi,ema55,atr_pct,cvd_z,oi_delta_pct,funding_z,flow,btc_mode,btc_coupling,data_age_sec\n"
                "ETH/USDT:USDT,100,3000,39,104,2,0.4,0.2,-0.1,long,neutral,coupled,10\n"
                "BAD/USDT:USDT,100,10,60,101,9,-2,0,1.2,hostile,hard_dump,coupled,10\n",
                encoding="utf-8",
            )
            summary = replay_csv(csv, BotConfig(journal_dir=Path(tmp) / "journal"))
            self.assertEqual(2, summary["cycles"])
            self.assertEqual(1, summary["entries"])
            self.assertEqual(1, summary["rejected"])
            self.assertTrue((Path(tmp) / "journal" / "gate_decision.jsonl").exists())


if __name__ == "__main__":
    unittest.main()
