import asyncio
import json

import numpy as np
import pandas as pd
import pytest

from fusion_quantum import paper_runner as runner
from fusion_quantum.paper_engine import intent_from_setup, submit_paper


def test_paper_intent_is_bounded_and_gateway_compatible():
    i = intent_from_setup({
        "symbol": "BTCUSDT", "side": "LONG", "entry_price": 100,
        "sl_price": 95, "tp_price": 110,
    })
    assert i.source == "TEST"
    assert i.risk_pct == 0.0025
    assert i.validate() is None
    assert i.tag == "fusion_quantum_h4_15m"


def test_confirmed_setup_matches_backtest_formula(monkeypatch):
    ltf = pd.DataFrame({
        "open_time": pd.date_range("2026-01-01", periods=15, freq="15min"),
        "open": np.full(15, 100.0), "high": np.full(15, 102.0),
        "low": np.full(15, 98.0), "close": np.full(15, 100.0),
        "volume": np.ones(15),
    })
    zone = {"t": pd.Timestamp("2025-12-31"), "zlow": 95.0, "zhigh": 105.0}
    imbalance = {"t": pd.Timestamp("2026-01-01"), "ce": 5, "leg_low": 90.0, "leg_high": 110.0}
    monkeypatch.setattr(runner, "_valid_obs", lambda *_: [zone])
    monkeypatch.setattr(runner, "_imbalances", lambda *_: [imbalance])
    monkeypatch.setattr(runner, "mss_confirm", lambda *_: {"i": 9, "sweep": 96.0, "disp": 106.0, "side": "BULL"})
    monkeypatch.setattr(runner, "_atr", lambda _: np.full(15, 4.0))
    ltf.loc[6, "low"] = 100.0

    setups = runner.confirmed_setups("BTCUSDT", pd.DataFrame(), ltf)

    assert len(setups) == 1
    assert setups[0]["entry_price"] == 101.59
    assert setups[0]["sl_price"] == 94.0
    assert setups[0]["tp_price"] == pytest.approx(116.77)
    assert setups[0]["expires_at"] == str(ltf.open_time.iloc[9] + pd.Timedelta(minutes=90))


def test_pending_limit_waits_fills_and_expires():
    setup = {"side": "LONG", "entry_price": 100.0, "confirmed_at": "2026-01-01 00:00:00", "expires_at": "2026-01-01 06:00:00"}
    assert runner.pending_action(setup, 101, 102, pd.Timestamp("2026-01-01 01:00:00")) == "wait"
    assert runner.pending_action(setup, 99, 102, pd.Timestamp("2026-01-01 01:00:00")) == "fill"
    assert runner.pending_action(setup, 99, 102, pd.Timestamp("2026-01-01 06:01:00")) == "expired"


def test_pending_cap_is_twenty(tmp_path):
    state = runner.State(tmp_path / "state.json")
    for i in range(20):
        state.set_status(str(i), "pending")
    assert state.pending_count() == 20
    assert not state.can_add_pending()


def test_state_persists_dedup_and_lifecycle(tmp_path):
    state = runner.State(tmp_path / "state.json")
    assert state.claim("setup-1")
    state.set_status("setup-1", "would_deploy")
    reloaded = runner.State(tmp_path / "state.json")
    assert not reloaded.claim("setup-1")
    assert reloaded.data["setups"]["setup-1"]["status"] == "would_deploy"


def test_gateway_result_only_accepts_opened_paper_position():
    assert runner.gateway_accepted({"ok": True, "status": "paper_opened", "would_deploy": True})
    assert not runner.gateway_accepted({"ok": True, "status": "would_deploy"})
    assert not runner.gateway_accepted({"ok": True, "reason": "DRY_RUN (no order placed)"})
    assert not runner.gateway_accepted({"ok": True, "status": "deployed"})


def test_submit_paper_requires_validation_then_opens(monkeypatch, tmp_path):
    class Client:
        def __init__(self): self.calls = []
        async def execute(self, intent, *, dry_run=False):
            self.calls.append(dry_run)
            return {"ok": True, "reason": "DRY_RUN (no order placed)"} if dry_run else {"ok": True, "reason": "opened"}
    client = Client()
    monkeypatch.setattr("fusion_quantum.paper_engine.AUDIT_PATH", tmp_path / "audit.jsonl")
    result = asyncio.run(submit_paper({"symbol": "BTCUSDT", "side": "LONG", "entry_price": 100, "sl_price": 95, "tp_price": 110}, client=client))
    assert client.calls == [True, False]
    assert result["status"] == "paper_opened"
    assert result["would_deploy"] is True


def test_jsonl_and_heartbeat_are_atomic(tmp_path):
    audit = tmp_path / "audit.jsonl"
    heartbeat = tmp_path / "heartbeat.json"
    runner.append_jsonl(audit, {"event": "scan", "count": 2})
    runner.write_heartbeat(heartbeat, {"status": "ok"})
    assert json.loads(audit.read_text()) == {"event": "scan", "count": 2}
    assert json.loads(heartbeat.read_text())["status"] == "ok"


def test_setup_message_matches_old_bot_shape():
    msg = runner.setup_message(
        {"symbol": "BTCUSDT", "side": "SHORT", "entry_price": 100.0, "sl_price": 102.0, "tp_price": 96.0},
        {"execution": {"trader_response": {"qty": 2.5, "entry_price": 99.5}}},
    )
    assert "🆕 SETUP H4 SELL BTCUSDT [fusion_quantum_h4_15m]" in msg
    assert "Entry 100 | SL 102 | TP 96 | qty 2.5 | RR 2.0" in msg
    assert "TradingView: https://www.tradingview.com/chart/?symbol=BTCUSDT.P" in msg
    assert "✅ FILLED BTCUSDT SELL @~99.5" in msg
