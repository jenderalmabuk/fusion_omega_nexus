import importlib.util
from pathlib import Path

P = Path(__file__).parents[1] / "fusion_quantum/backtest_quantum.py"
spec = importlib.util.spec_from_file_location("backtest_quantum", P)
q = importlib.util.module_from_spec(spec)
spec.loader.exec_module(q)


def test_zone_can_only_start_one_retest():
    lifecycle = q.ZoneLifecycle()
    zone = {"t": 10, "zlow": 100.0, "zhigh": 101.0}

    assert lifecycle.start_retest("BULL", zone)
    assert not lifecycle.start_retest("BULL", zone)
    assert lifecycle.start_retest("BEAR", zone)


def test_timestamp_zone_key_is_supported():
    import pandas as pd

    lifecycle = q.ZoneLifecycle()
    zone = {"t": pd.Timestamp("2026-07-25T00:00:00Z"), "zlow": 100.0, "zhigh": 101.0}

    assert lifecycle.start_retest("BULL", zone)
    assert not lifecycle.start_retest("BULL", zone)


def test_price_pnl_is_normalized_by_initial_risk():
    assert q.pnl_r(20.0, 10.0) == 2.0


def test_resample_builds_complete_ohlcv_candle():
    import pandas as pd

    df = pd.DataFrame({
        "open_time": pd.date_range("2026-01-01", periods=3, freq="5min", tz="UTC"),
        "open": [1.0, 2.0, 3.0], "high": [2.0, 4.0, 5.0],
        "low": [0.0, 1.0, 2.0], "close": [1.5, 3.0, 4.0],
        "volume": [10.0, 20.0, 30.0],
    })

    got = q.resample_ohlcv(df, "15min")

    assert len(got) == 1
    assert got.iloc[0][["open", "high", "low", "close", "volume"]].tolist() == [1.0, 5.0, 0.0, 4.0, 60.0]


def test_walk_forward_uses_sequential_time_windows():
    trades = [{"entry_time": f"2026-01-{d:02d}", "pnl_unit": float(d % 2)} for d in range(1, 11)]

    folds = q.walk_forward(trades, folds=2)

    assert [(x["train"]["n"], x["test"]["n"]) for x in folds] == [(4, 2), (6, 2)]
    assert folds[0]["test_start"] == "2026-01-05"
    assert folds[1]["test_end"] == "2026-01-08"


def test_walk_forward_never_shares_timestamp_between_train_and_test():
    trades = [
        {"entry_time": "2026-01-01", "pnl_unit": 1.0},
        {"entry_time": "2026-01-02", "pnl_unit": 1.0},
        {"entry_time": "2026-01-02", "pnl_unit": -1.0},
        {"entry_time": "2026-01-03", "pnl_unit": 1.0},
        {"entry_time": "2026-01-04", "pnl_unit": -1.0},
        {"entry_time": "2026-01-05", "pnl_unit": 1.0},
    ]

    for fold in q.walk_forward(trades, folds=2):
        assert fold["train_end"] < fold["test_start"]


def test_funnel_counts_terminal_paths():
    lifecycle = q.ZoneLifecycle()
    zone = {"t": 10, "zlow": 100.0, "zhigh": 101.0}

    lifecycle.found("BULL", zone)
    lifecycle.start_retest("BULL", zone)
    lifecycle.mark("mss_confirmed")
    lifecycle.mark("pending_orders")
    lifecycle.mark("fills")

    assert lifecycle.funnel == {
        "zones_found": 1,
        "zones_touched": 1,
        "mss_confirmed": 1,
        "pending_orders": 1,
        "fills": 1,
        "expired_orders": 0,
    }
