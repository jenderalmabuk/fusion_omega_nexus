import sys
import types
import unittest
from unittest.mock import patch

fake_db = types.ModuleType("collector.db")
for name in ("last_oi", "upsert_funding", "upsert_klines", "upsert_oi", "upsert_universe"):
    setattr(fake_db, name, None)
sys.modules.setdefault("collector.db", fake_db)

from collector import binance


class BinanceSymbolQuarantineTest(unittest.TestCase):
    def setUp(self):
        binance._unsupported_until.clear()

    def test_invalid_symbol_is_quarantined_then_retried(self):
        with patch("collector.binance.time.time", return_value=100.0):
            binance._quarantine("AERGOUSDT")
            self.assertTrue(binance._is_quarantined("AERGOUSDT"))
        with patch("collector.binance.time.time", return_value=100.0 + binance.UNSUPPORTED_RETRY_SEC + 1):
            self.assertFalse(binance._is_quarantined("AERGOUSDT"))

    def test_other_symbols_are_not_quarantined(self):
        self.assertFalse(binance._is_quarantined("BTCUSDT"))


if __name__ == "__main__":
    unittest.main()
