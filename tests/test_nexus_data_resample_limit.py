"""Regression: 3m synthesis must respect Nexus API max limit=2000."""
from __future__ import annotations

import pandas as pd

from bots import nexus_data


def test_resample_caps_1m_request_to_api_limit(monkeypatch) -> None:
    requested: list[tuple[str, str, int]] = []

    def fake_fetch(symbol: str, interval: str = "1h", limit: int = 300) -> pd.DataFrame:
        requested.append((symbol, interval, limit))
        rows = min(limit, 2000)
        ts = pd.date_range("2026-01-01", periods=rows, freq="1min")
        return pd.DataFrame({
            "open_time": ts,
            "open": 1.0,
            "high": 1.0,
            "low": 1.0,
            "close": 1.0,
            "volume": 1.0,
            "taker_buy_base": 0.5,
        })

    monkeypatch.setattr(nexus_data, "fetch_recent", fake_fetch)
    out = nexus_data._resample_1m("AERGOUSDT", "3m", 1000)

    assert requested == [("AERGOUSDT", "1m", 2000)]
    assert len(out) >= 260
    assert out["open_time"].iloc[-1] > out["open_time"].iloc[0]
