"""Unit tests for pure helper functions in api/main.py."""

import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost:5432/test")

from api.main import _ema, _parse_tf, _zscore  # noqa: E402


class TestParseTf:
    def test_minutes(self):
        assert _parse_tf("1m") == "1m"
        assert _parse_tf("5m") == "5m"
        assert _parse_tf("15m") == "15m"
        assert _parse_tf("30m") == "30m"

    def test_hours(self):
        assert _parse_tf("1h") == "1h"
        assert _parse_tf("4h") == "4h"

    def test_days(self):
        assert _parse_tf("1d") == "1d"

    def test_uppercase(self):
        assert _parse_tf("1H") == "1h"
        assert _parse_tf("15M") == "15m"

    def test_all_supported_timeframes(self):
        for tf in ("1m", "3m", "5m", "15m", "30m", "1h", "4h", "1d"):
            assert _parse_tf(tf) == tf


class TestEma:
    def test_constant_series(self):
        data = np.array([10.0] * 50)
        assert _ema(data, 10) == pytest.approx(10.0)

    def test_trending_series(self):
        data = np.linspace(1, 100, 100)
        ema = _ema(data, 10)
        # EMA lags a rising series: below last value, above the mean
        assert ema < 100
        assert ema > 50


class TestZscore:
    def test_short_array_returns_zero(self):
        assert _zscore(np.array([1.0, 2.0])) == 0.0

    def test_zero_std_returns_zero(self):
        assert _zscore(np.array([5.0, 5.0, 5.0, 5.0])) == 0.0

    def test_outlier_positive(self):
        data = np.array([1.0, 1.0, 1.0, 1.0, 10.0])
        assert _zscore(data) > 1.0

    def test_outlier_negative(self):
        data = np.array([10.0, 10.0, 10.0, 10.0, 1.0])
        assert _zscore(data) < -1.0
