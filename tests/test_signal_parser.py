"""Unit tests for signal_copy.signal_parser.parse_signal."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from signal_copy.signal_parser import parse_signal  # noqa: E402
from signal_copy.signal_schema import SignalSide  # noqa: E402


class TestParseSignalRejects:
    def test_empty_text(self):
        assert parse_signal("") is None
        assert parse_signal("   ") is None

    def test_plain_chatter(self):
        assert parse_signal("gm everyone, market looks choppy today") is None

    def test_news_message(self):
        assert parse_signal("Breaking: ETF inflows hit record highs this week") is None


class TestParseSignalAccepts:
    def test_standard_long_signal(self):
        text = (
            "Pair: ZEC/USDT\n"
            "Position: LONG\n"
            "Leverage: 10x\n"
            "Entry: 358 - 350\n"
            "Stop Loss: 340\n"
            "TP1: 370\nTP2: 385\n"
        )
        sig = parse_signal(text)
        assert sig is not None
        assert sig.symbol == "ZECUSDT"
        assert sig.side == SignalSide.LONG
        assert sig.stop_loss == 340
        assert len(sig.take_profits) >= 2

    def test_short_signal_concat_pair(self):
        text = (
            "BTCUSDT SHORT\n"
            "Entry: 97000 - 97500\n"
            "SL: 98200\n"
            "TP: 95000\n"
        )
        sig = parse_signal(text)
        assert sig is not None
        assert sig.symbol == "BTCUSDT"
        assert sig.side == SignalSide.SHORT

    def test_never_raises_on_garbage(self):
        # Parser contract: return None on bad input, never raise
        for garbage in ["\x00\x01", "LONG " * 500, "🚀🚀🚀", "entry sl tp"]:
            parse_signal(garbage)
