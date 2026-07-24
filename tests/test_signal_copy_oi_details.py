from fusionnew.clean_core.adversarial import _build_data_block
from nexus.data_bridge import NexusDataBridge
from signal_copy.telegram_formatter import build_parser_report


class _DummyResp:
    def raise_for_status(self):
        return None

    def json(self):
        return {
            "result": {
                "list": [
                    {"timestamp": 1000, "openInterest": "100.0"},
                    {"timestamp": 1300, "openInterest": "101.0"},
                    {"timestamp": 1600, "openInterest": "102.0"},
                    {"timestamp": 1900, "openInterest": "121.0"},
                    {"timestamp": 2200, "openInterest": "133.1"},
                ]
            }
        }


class _DummyRequests:
    @staticmethod
    def get(*args, **kwargs):
        return _DummyResp()


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


def test_load_oi_5m_raw_falls_back_for_universe_miss(monkeypatch):
    import sys

    bridge = NexusDataBridge.__new__(NexusDataBridge)
    monkeypatch.setitem(sys.modules, "requests", _DummyRequests)
    monkeypatch.setattr("nexus.data_bridge.Path.exists", lambda self: False)
    monkeypatch.setattr("nexus.data_bridge._canonical_supported", lambda symbol: True)

    out = bridge._load_oi_5m_raw("RIFUSDT")

    assert out["oi_source"] == "bybit_direct_5m"
    assert out["oi_change_5m_pct"] == 10.0
    assert out["oi_change_15m_pct"] == 31.7822


def test_signal_outside_canonical_is_hard_rejected(monkeypatch):
    from signal_copy.signal_schema import ParsedSignal, SignalSide
    from signal_copy.validation_engine import Verdict, validate_signal

    monkeypatch.setenv("SIGNAL_COPY_LEGACY_VALIDATION", "0")
    monkeypatch.setattr("signal_copy.validation_engine._canonical_supported", lambda symbol: False)
    sig = ParsedSignal("RIFUSDT", SignalSide.LONG, 0.089, 0.0915, stop_loss=0.0865, take_profits=[0.0945])
    out = validate_signal(sig, {"data_valid": True, "price": 0.1289})

    assert out.verdict == Verdict.REJECT
    assert "canonical" in out.hard_blocks[0]


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
