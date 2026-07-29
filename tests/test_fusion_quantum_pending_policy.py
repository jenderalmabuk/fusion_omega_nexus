import tempfile
import unittest
from pathlib import Path

from fusion_quantum.paper_runner import State, lifecycle_claimed, lifecycle_key, reconcile_pending


def test_zone_lifecycle_blocks_repeated_confirmation():
    state = State(Path(tempfile.mkdtemp()) / "state.json")
    first = {"symbol": "BTCUSDT", "side": "LONG", "zone_key": ["BULL", "2026-01-01", 99.0, 100.0]}
    repeated = {"symbol": "BTCUSDT", "side": "LONG", "zone_key": ["BULL", "2026-01-01", 99.0, 100.0]}
    state.data["setups"]["first"] = {"status": "expired", "setup": first}

    assert lifecycle_key(first) == lifecycle_key(repeated)
    assert lifecycle_claimed(state, repeated)


def test_different_zone_is_allowed():
    state = State(Path(tempfile.mkdtemp()) / "state.json")
    state.data["setups"]["first"] = {"status": "paper_opened", "setup": {"zone_key": ["BEAR", "2026-01-01", 100.0, 101.0]}}

    assert not lifecycle_claimed(state, {"zone_key": ["BEAR", "2026-01-02", 100.0, 101.0]})


def test_error_does_not_consume_zone():
    state = State(Path(tempfile.mkdtemp()) / "state.json")
    setup = {"zone_key": ["BULL", "2026-01-01", 99.0, 100.0]}
    state.data["setups"]["first"] = {"status": "error", "setup": setup}

    assert not lifecycle_claimed(state, setup)


class PendingPolicyTest(unittest.TestCase):
    def state(self):
        return State(Path(tempfile.mkdtemp()) / "state.json")

    def test_running_symbol_cancels_existing_pending(self):
        state = self.state()
        state.data["setups"] = {
            "old": {"status": "pending", "setup": {"symbol": "MIRAUSDT", "side": "LONG"}}
        }

        changed = reconcile_pending(state, {"MIRAUSDT"})

        self.assertEqual(changed, 1)
        self.assertEqual(state.data["setups"]["old"]["status"], "blocked_running")

    def test_new_setup_supersedes_older_pending_same_symbol(self):
        state = self.state()
        state.data["setups"] = {
            "old": {"status": "pending", "setup": {"symbol": "CATIUSDT", "side": "LONG"}}
        }

        changed = reconcile_pending(state, set(), symbol="CATIUSDT", keep_id="new")

        self.assertEqual(changed, 1)
        self.assertEqual(state.data["setups"]["old"]["status"], "superseded")

    def test_unrelated_pending_survives(self):
        state = self.state()
        state.data["setups"] = {
            "old": {"status": "pending", "setup": {"symbol": "OTHERUSDT", "side": "LONG"}}
        }

        self.assertEqual(reconcile_pending(state, set(), symbol="CATIUSDT", keep_id="new"), 0)
        self.assertEqual(state.data["setups"]["old"]["status"], "pending")


if __name__ == "__main__":
    unittest.main()
