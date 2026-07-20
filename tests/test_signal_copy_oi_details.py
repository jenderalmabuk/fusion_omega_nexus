from fusionnew.clean_core.adversarial import _build_data_block
from nexus.data_bridge import NexusDataBridge
from signal_copy.telegram_formatter import build_parser_report


class _Side:
    value = "LONG"


class _Sig:
    symbol = "BTCUSDT"
    side = _Side()
    entry_low = 100.0
    entry_high = 101.0
    entry_mid = 100.5
    stop_loss = 99.0
    take_profits = [102.0]
    leverage = 10
    risk_pct = 0.0
    timeframe = "15m"


class _Verdict:
    value = "VALID"


class _Result:
    verdict = _Verdict()
    score = 80.0
    hard_blocks = []
    metrics_snapshot = {
        "price": 100.0,
        "rsi": 42.0,
        "cvd_zscore": 0.3,
        "oi_change_5m_pct": 1.23,
        "oi_change_15m_pct": -0.45,
        "oi_change_1h_pct": 2.34,
        "funding_rate": 0.01,
        "regime_label": "TRENDING",
    }


def test_calc_oi_changes_includes_5m_delta():
    bridge = NexusDataBridge.__new__(NexusDataBridge)
    bridge._oi_history = {"BTCUSDT": {1000: 100.0, 3700: 110.0, 4300: 121.0, 4600: 133.1}}
    bridge._build_oi_history = lambda: bridge._oi_history

    out = bridge._calc_oi_changes("BTCUSDT", {"oi": {"BTCUSDT": {"bybit": 133.1}}})

    assert out["oi_change_5m_pct"] == 10.0
    assert out["oi_change_15m_pct"] == 21.0
    assert out["oi_change_1h_pct"] == 33.1


def test_parser_report_shows_oi_5m_15m_1h():
    msg = build_parser_report(_Sig(), _Result())

    assert "OI 5m/15m/1h:" in msg
    assert "+1.23%/-0.45%/+2.34%" in msg


def test_adversarial_data_block_includes_oi_mtf_tv():
    block = _build_data_block({
        "price": 0.0016565,
        "timeframe": "15m",
        "leverage": 50,
        "rsi": 55.0,
        "cvd_zscore": 1.16,
        "oi_change_5m_pct": 0.0,
        "oi_change_15m_pct": 0.0,
        "oi_change_1h_pct": 0.3183,
        "funding_rate": 0.0001,
        "flow_direction": "NO_TRADE",
        "qvol_5m": 72052631.0,
        "data_quality": "OK",
        "regime_label": "HIGH_VOL",
        "mtf_score": 85.0,
        "tv_score": 0.0,
        "validation_score": 84.5,
    })

    assert "price=0.0016565" in block
    assert "tf=15m" in block
    assert "lev=50x" in block
    assert "OI 5m/15m/1h=+0.00%/+0.00%/+0.32%" in block
    assert "MTF=85/100" in block
    assert "TV=0/100" in block
    assert "confluence=84/100" in block
