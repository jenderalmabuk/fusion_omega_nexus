import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class Side:
    value = "SHORT"


class Sig:
    symbol = "ESPORTSUSDT"
    side = Side()
    source_chat_id = -1001652601224
    signal_id = "abc123"
    entry_low = 0.0342
    entry_high = 0.0352
    active_entry = 0.0352
    stop_loss = 0.036782
    take_profits = [0.032618, 0.030536]


class PendingJournalTest(unittest.TestCase):
    def test_lifecycle_events_are_appended_with_full_setup(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "pending.jsonl"
            with patch.dict(os.environ, {"SIGNAL_COPY_PENDING_JOURNAL": str(path)}):
                import importlib
                import signal_copy.pending_journal as journal
                journal = importlib.reload(journal)

                journal.record("PENDING", Sig(), price=0.03585, reason="PRICE_OUTSIDE_ENTRY_ZONE")
                journal.record("DRIFT_REJECTED", Sig(), price=0.03315, drift_r=1.30)

                rows = [json.loads(line) for line in path.read_text().splitlines()]

        self.assertEqual([r["event"] for r in rows], ["PENDING", "DRIFT_REJECTED"])
        self.assertEqual(rows[0]["symbol"], "ESPORTSUSDT")
        self.assertEqual(rows[0]["side"], "SHORT")
        self.assertEqual(rows[0]["stop_loss"], 0.036782)
        self.assertEqual(rows[0]["take_profits"], [0.032618, 0.030536])
        self.assertEqual(rows[1]["drift_r"], 1.30)
        self.assertIn("ts", rows[0])

    def test_load_active_returns_only_unexpired_latest_pending(self):
        from datetime import datetime, timezone
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "pending.jsonl"
            now = datetime.now(timezone.utc).isoformat()
            rows = [
                {"ts": now, "event": "PENDING", "signal_id": "live", "symbol": "ETHUSDT",
                 "side": "LONG", "source_chat_id": 1, "entry_low": 100, "entry_high": 101,
                 "active_entry": 101, "stop_loss": 90, "take_profits": [110], "created_epoch": 999},
                {"ts": now, "event": "PENDING", "signal_id": "done", "symbol": "BTCUSDT",
                 "side": "SHORT", "source_chat_id": 2, "entry_low": 100, "entry_high": 100,
                 "stop_loss": 110, "take_profits": [90]},
                {"ts": now, "event": "FILLED", "signal_id": "done", "symbol": "BTCUSDT"},
            ]
            path.write_text("".join(json.dumps(r) + "\n" for r in rows))
            with patch.dict(os.environ, {"SIGNAL_COPY_PENDING_JOURNAL": str(path)}):
                import importlib
                import signal_copy.pending_journal as journal
                journal = importlib.reload(journal)
                active = journal.load_latest_pending()
        self.assertEqual([r["signal_id"] for r in active], ["live"])

    def test_pending_age_uses_persisted_creation_time(self):
        import time
        from signal_copy.pending_journal import row_age_seconds
        self.assertGreater(row_age_seconds({"created_epoch": time.time() - 120}), 119)

    def test_restore_rows_rebuilds_signal_geometry(self):
        from signal_copy.pending_journal import signal_from_row
        sig = signal_from_row({
            "signal_id": "restore", "symbol": "ETHUSDT", "side": "LONG",
            "source_chat_id": 123, "entry_low": 100, "entry_high": 101,
            "active_entry": 101, "stop_loss": 90, "take_profits": [110, 120],
            "source_name": "provider", "raw_text": "original",
        })
        self.assertEqual(sig.signal_id, "restore")
        self.assertEqual(sig.side.value, "LONG")
        self.assertEqual(sig.active_entry, 101)
        self.assertEqual(sig.take_profits, [110.0, 120.0])

    def test_record_never_raises(self):
        with patch.dict(os.environ, {"SIGNAL_COPY_PENDING_JOURNAL": "/proc/cannot/write.jsonl"}):
            import importlib
            import signal_copy.pending_journal as journal
            journal = importlib.reload(journal)
            journal.record("PENDING", Sig())  # must not raise


if __name__ == "__main__":
    unittest.main()
