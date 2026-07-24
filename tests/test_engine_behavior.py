"""5.2c/d — engine lifecycle: PENDING expiry without price data, and
signal NOT consumed (_seen) when _open_pending raises."""
import time

import pandas as pd
import pytest

import clean_core.engine as eng_mod
from clean_core.engine import Engine, RiskGuard
from clean_core.executor import FuturesTestnet


def make_engine(tmp_path, monkeypatch, tier: str = "M30") -> Engine:
    monkeypatch.setattr(eng_mod, "STATE_DIR", tmp_path)
    monkeypatch.setattr(eng_mod, "tg", lambda *_a, **_k: None)
    ex = FuturesTestnet(dry=True)
    risk = RiskGuard(equity_ref=1000.0, risk_pct=0.01, max_positions=4,
                     max_notional_mult=3.0, daily_loss_limit_pct=0.05)
    return Engine(ex, risk, tier=tier, rr=3.0, leverage=5, dry=True)


def test_pending_expires_with_empty_price_data(tmp_path, monkeypatch):
    """5.2c — a stale PENDING must be cancelled even when 1m data is empty."""
    engine = make_engine(tmp_path, monkeypatch)
    empty = pd.DataFrame(columns=["open_time", "open", "high", "low", "close", "volume"])
    monkeypatch.setattr(eng_mod, "fetch_recent", lambda *a, **k: empty)
    engine.trades.append({
        "symbol": "TESTUSDT", "tier": "M30", "side": "BUY", "imb_side": "BULL",
        "entry": 100.0, "sl": 99.0, "tp": 103.0, "qty": 1.0, "status": "PENDING",
        "t_complete": "2026-01-01 00:00:00",
        "expiry_min": 60,                       # FIB_EXPIRY(12) * 5m
        "opened_at": time.time() - 2 * 3600,    # 2h old > 60min expiry
    })
    engine.manage("TESTUSDT")
    assert engine.trades[0]["status"] == "CANCELLED"


def test_fresh_pending_not_expired(tmp_path, monkeypatch):
    engine = make_engine(tmp_path, monkeypatch)
    empty = pd.DataFrame(columns=["open_time", "open", "high", "low", "close", "volume"])
    monkeypatch.setattr(eng_mod, "fetch_recent", lambda *a, **k: empty)
    engine.trades.append({
        "symbol": "TESTUSDT", "tier": "M30", "side": "BUY", "imb_side": "BULL",
        "entry": 100.0, "sl": 99.0, "tp": 103.0, "qty": 1.0, "status": "PENDING",
        "t_complete": "2026-01-01 00:00:00",
        "expiry_min": 60,
        "opened_at": time.time() - 5 * 60,      # only 5 min old
    })
    engine.manage("TESTUSDT")
    assert engine.trades[0]["status"] == "PENDING"


def test_pending_ignores_pre_order_bar(tmp_path, monkeypatch):
    """F-02 — replayed candles before order creation must not fill a pending order."""
    engine = make_engine(tmp_path, monkeypatch)
    opened_at = time.time()
    trade = {
        "symbol": "TESTUSDT", "tier": "M30", "side": "BUY", "imb_side": "BULL",
        "entry": 100.0, "sl": 99.0, "tp": 103.0, "qty": 1.0, "status": "PENDING",
        "t_complete": "2026-01-01 00:00:00", "expiry_min": 60,
        "opened_at": opened_at,
    }
    engine.trades.append(trade)

    engine._manage_pending(trade, "TESTUSDT", hi=101.0, lo=99.0, bar_epoch=opened_at - 60)
    assert trade["status"] == "PENDING"

    engine._manage_pending(trade, "TESTUSDT", hi=101.0, lo=99.0, bar_epoch=opened_at + 60)
    assert trade["status"] == "OPEN"


def test_seen_not_consumed_on_open_pending_exception(tmp_path, monkeypatch):
    """5.2d — a technical failure in _open_pending must NOT mark the signal
    as seen; it must stay retryable next cycle."""
    from backtest.faithful_imbalance import _trend
    from tests.synthetic import make_ltf_df, make_zone_df

    engine = make_engine(tmp_path, monkeypatch)
    zone, ltf = make_zone_df(), make_ltf_df()

    def fake_fetch(symbol, interval, limit=300):
        df = (zone if interval in ("30m", "1h", "4h", "15m") else ltf).copy()
        df.attrs["stale"] = False
        df.attrs["lag_sec"] = 0.0
        return df

    monkeypatch.setattr(eng_mod, "fetch_recent", fake_fetch)

    calls = {"n": 0}

    def boom(symbol, s):
        calls["n"] += 1
        raise ConnectionError("simulated exchangeInfo/network failure")

    monkeypatch.setattr(engine, "_open_pending", boom)
    engine.scan_symbol("SYNTHUSDT")
    assert calls["n"] == 1, "signal should have reached _open_pending"
    assert len(engine._seen) == 0, "exception must NOT consume the signal"
    assert engine._stats.get("open_pending_err", 0) == 1

    # Next cycle: same signal is retried and, on success, marked seen.
    opened = {}
    monkeypatch.setattr(engine, "_open_pending", lambda sym, s: opened.update(s))
    engine.scan_symbol("SYNTHUSDT")
    assert opened, "signal must be retried the next cycle"
    assert len(engine._seen) == 1


def test_stale_guard_skips_scan(tmp_path, monkeypatch):
    """5.2e — a symbol whose feed is stale must be skipped and counted."""
    engine = make_engine(tmp_path, monkeypatch)
    from tests.synthetic import make_ltf_df, make_zone_df
    zone, ltf = make_zone_df(), make_ltf_df()

    def fake_fetch(symbol, interval, limit=300):
        df = (zone if interval in ("30m", "1h", "4h", "15m") else ltf).copy()
        df.attrs["stale"] = True
        df.attrs["lag_sec"] = 99999.0
        return df

    monkeypatch.setattr(eng_mod, "fetch_recent", fake_fetch)
    engine.scan_symbol("STALEUSDT")
    assert engine._stats["stale"] == 1
    assert engine._stats["cand"] == 0


def test_dry_round_qty_never_calls_network(monkeypatch):
    """4.4 — dry-mode rounding must never hit testnet exchangeInfo."""
    ex = FuturesTestnet(dry=True)

    def boom(*a, **k):
        raise AssertionError("network call in dry mode")

    monkeypatch.setattr(ex, "_public", boom)
    assert ex.round_qty("OBSCUREUSDT", 1.23456789) == pytest.approx(1.234568)
    assert ex.round_price("OBSCUREUSDT", 0.000123456789) == pytest.approx(0.000123)
