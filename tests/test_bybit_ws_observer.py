import asyncio
import unittest
from datetime import datetime, timezone

from scripts.bybit_ws_observer import ClosedCandleDeduper, parse_kline, reconnect_delay, maybe_call
from scripts.fq_ws_shadow import candle_ready, snapshot_at, unique_setups


def test_snapshot_at_removes_future_bars():
    import pandas as pd
    df = pd.DataFrame({"open_time": pd.to_datetime(["2026-07-28T00:00:00Z", "2026-07-28T00:15:00Z"]), "close": [1.0, 2.0]})

    got = snapshot_at(df, 1785196800000)

    assert len(got) == 1
    assert float(got.iloc[-1]["close"]) == 1.0


class BybitObserverTest(unittest.TestCase):
    def test_closed_kline_emits_event(self):
        msg = {"topic": "kline.15.BTCUSDT", "data": [{"start": 1000, "confirm": True}]}
        event = parse_kline(msg)
        self.assertEqual(event["symbol"], "BTCUSDT")
        self.assertTrue(event["closed"])

    def test_forming_kline_is_ignored(self):
        msg = {"topic": "kline.15.BTCUSDT", "data": [{"start": 1000, "confirm": False}]}
        self.assertIsNone(parse_kline(msg))

    def test_duplicate_candle_emits_once(self):
        d = ClosedCandleDeduper()
        self.assertTrue(d.accept("BTCUSDT", 1000))
        self.assertFalse(d.accept("BTCUSDT", 1000))
        self.assertTrue(d.accept("BTCUSDT", 1900))

    def test_reconnect_backoff_is_bounded(self):
        self.assertEqual([reconnect_delay(i) for i in range(6)], [1, 2, 4, 8, 16, 30])

    def test_closed_event_calls_shadow_callback(self):
        seen = []
        asyncio.run(maybe_call(lambda event: seen.append(event["symbol"]), {"symbol": "BTCUSDT"}))
        self.assertEqual(seen, ["BTCUSDT"])

    def test_shadow_requires_expected_candle(self):
        self.assertTrue(candle_ready("2026-07-26 14:45:00", 1785077100000))
        self.assertFalse(candle_ready("2026-07-26 14:30:00", 1785077100000))

    def test_shadow_setup_ids_are_unique(self):
        rows = [{"id": 1}, {"id": 1}, {"id": 2}]
        self.assertEqual(unique_setups(rows, lambda x: str(x["id"])), rows[::2])


if __name__ == "__main__":
    unittest.main()


def _unused() -> datetime:
    return datetime.now(timezone.utc)
