from signal_copy.signal_schema import ParsedSignal, SignalSide
from signal_copy.validation_engine import validate_signal


def _sig() -> ParsedSignal:
    return ParsedSignal(
        symbol="ESPORTSUSDT",
        side=SignalSide.LONG,
        entry_low=0.035,
        entry_high=0.036,
        stop_loss=0.034,
        take_profits=[0.0385, 0.041, 0.046],
        leverage=50,
    )


def test_validation_selects_nearest_active_entry_from_live_price():
    sig = _sig()

    validate_signal(sig, {"data_valid": True, "price": 0.0359})

    assert sig.active_entry == 0.036
    assert round(sig.rr_ratio(0), 2) == 1.25
    assert round(sig.rr_best(), 2) == 5.00


def test_validation_selects_lower_entry_when_price_is_closer_to_lower_level():
    sig = _sig()

    validate_signal(sig, {"data_valid": True, "price": 0.0351})

    assert sig.active_entry == 0.035
    assert round(sig.rr_ratio(0), 2) == 3.50
    assert round(sig.rr_best(), 2) == 11.00
