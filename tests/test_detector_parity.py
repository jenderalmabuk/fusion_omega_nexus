"""5.2a — recent_setups must produce entry/sl/tp identical to generate_setups
on the same synthetic dataset containing one valid OB+FVG pattern."""
import pytest

from backtest.faithful_imbalance import FIB_EXPIRY, _trend, generate_setups, nearest_unmitigated_setups, recent_setups
from tests.synthetic import make_ltf_df, make_zone_df


@pytest.fixture(scope="module")
def frames():
    zone = make_zone_df()
    ltf = make_ltf_df()
    trend = _trend(zone)
    return zone, ltf, trend


def test_pattern_detected_by_both(frames):
    zone, ltf, trend = frames
    bt = generate_setups(zone, ltf, trend, "BULL")
    live = recent_setups(zone, ltf, trend, "BULL", max_age=FIB_EXPIRY)
    assert bt, "backtest detector missed the synthetic OB+FVG pattern"
    assert live, "live detector missed the synthetic OB+FVG pattern"


def test_entry_sl_tp_identical(frames):
    zone, ltf, trend = frames
    bt = generate_setups(zone, ltf, trend, "BULL")
    live = recent_setups(zone, ltf, trend, "BULL", max_age=FIB_EXPIRY)
    b, l = bt[-1], live[-1]
    assert b["ce"] == l["ce"]
    assert b["entry"] == pytest.approx(l["entry"], rel=1e-12)
    assert b["sl"] == pytest.approx(l["sl"], rel=1e-12)
    assert b["tp"] == pytest.approx(l["tp"], rel=1e-12)
    assert str(b["t_complete"]) == str(l["t_complete"])


def test_live_respects_max_age(frames):
    zone, ltf, trend = frames
    # Fresh-only detector returns the synthetic setup at default window,
    # but shrinking max_age below the imbalance age must drop it.
    assert recent_setups(zone, ltf, trend, "BULL", max_age=FIB_EXPIRY)
    assert not recent_setups(zone, ltf, trend, "BULL", max_age=1)


def test_nearest_unmitigated_keeps_older_unfilled_setup(frames):
    zone, ltf, trend = frames
    ltf = ltf.copy()
    # Make the post-imbalance path stay above the entry zone so the setup remains unmitigated.
    ltf.loc[397:, ["open", "high", "low", "close"]] = [120.5, 120.8, 120.4, 120.6]
    out = nearest_unmitigated_setups(zone, ltf, trend, "BULL")
    assert out, "nearest-unmitigated detector should keep the synthetic unfilled imbalance"
    assert out[0]["age_bars"] >= 0
    assert out[0]["dist_pct"] >= 0
