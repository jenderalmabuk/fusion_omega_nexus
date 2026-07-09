"""5.2b/e — nexus_data adapter: column mapping and stale-data guard."""
import datetime as dt

from bots.nexus_data import _to_df, fetch_recent


def _rows(open_time: dt.datetime, n: int = 3):
    return [
        {
            "open_time": (open_time + dt.timedelta(minutes=i)).isoformat(),
            "open": 100.0 + i, "high": 101.0 + i, "low": 99.0 + i,
            "close": 100.5 + i, "volume": 1000.0,
            "taker_buy_vol": 600.0, "quote_vol": 100500.0,
        }
        for i in range(n)
    ]


def test_taker_buy_vol_mapped_not_zero():
    """API column taker_buy_vol MUST land in taker_buy_base (CVD source)."""
    df = _to_df(_rows(dt.datetime(2026, 1, 1)), symbol="TESTUSDT", tf="1m")
    assert "taker_buy_base" in df.columns
    assert float(df["taker_buy_base"].sum()) == 600.0 * 3  # NOT silently 0
    assert "quote_volume" in df.columns
    assert float(df["quote_volume"].iloc[0]) == 100500.0


def test_missing_columns_still_zero_filled():
    rows = [{"open_time": "2026-01-01T00:00:00", "open": 1, "high": 1,
             "low": 1, "close": 1, "volume": 0.0}]
    df = _to_df(rows)
    assert float(df["taker_buy_base"].iloc[0]) == 0.0


def test_stale_guard_flags_frozen_feed(monkeypatch):
    """5.2e — a feed whose last candle is >3x TF old must be marked stale."""
    import bots.nexus_data as nd

    old = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None) - dt.timedelta(hours=2)

    class FakeResp:
        status_code = 200
        def json(self):
            return {"count": 3, "data": _rows(old)}

    class FakeClient:
        def get(self, *a, **kw):
            return FakeResp()

    monkeypatch.setattr(nd, "_get_client", lambda: FakeClient())
    df = fetch_recent("FROZENUSDT", "1m", 10)
    assert df.attrs["stale"] is True
    assert df.attrs["lag_sec"] > 3 * 60


def test_fresh_feed_not_stale(monkeypatch):
    import bots.nexus_data as nd

    recent = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None) - dt.timedelta(seconds=90)

    class FakeResp:
        status_code = 200
        def json(self):
            return {"count": 3, "data": _rows(recent)}

    class FakeClient:
        def get(self, *a, **kw):
            return FakeResp()

    monkeypatch.setattr(nd, "_get_client", lambda: FakeClient())
    df = fetch_recent("FRESHUSDT", "1m", 10)
    assert df.attrs["stale"] is False
