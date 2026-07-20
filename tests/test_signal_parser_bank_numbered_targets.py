from signal_copy.signal_parser import parse_signal


def test_bank_numbered_entry_tp_stop_signal():
    text = """#BANK/USDT
SIGNAL Type: Regular (LONG)
Leverage: Cross (50X)
Amount: 5%

Entry Targets:

1)  0.1100

2) 0.1065

Take-Profit Targets:

1) 0.1160

2)  0.1200

3)  0.1400

Stop Target:

1)  0.1035

Trailing Configuration:
Stop: Breakeven -
  Trigger: Target (1)
"""

    sig = parse_signal(text)

    assert sig is not None
    assert sig.symbol == "BANKUSDT"
    assert sig.entry_low == 0.1065
    assert sig.entry_high == 0.11
    assert sig.stop_loss == 0.1035
    assert sig.take_profits == [0.116, 0.12, 0.14]
    assert sig.leverage == 50.0
