from signal_copy.signal_parser import parse_signal


def test_ake_style_leverage_range_targets_and_stop_loss():
    text = """🛡WHALES MEGA SIGNALS✅

📌Pair: AKE/USDT
Type: LONG📈📈
✔️Leverage:  (10X_100X)

📊ENTRY ZONE:

1) 0.0019
2) 0.0016

📌 TARGETS:

1) 0.0025
2) 0.0032
3) 0.0040
4) 0.0055
5) 0.0080

❌ STOP LOSS: 0.0013❌

▶️FOR VIP Details: @Whales_Pumps_Owner"""

    sig = parse_signal(text)

    assert sig is not None
    assert sig.symbol == "AKEUSDT"
    assert sig.entry_low == 0.0016
    assert sig.entry_high == 0.0019
    assert sig.stop_loss == 0.0013
    assert sig.take_profits == [0.0025, 0.0032, 0.004, 0.0055, 0.008]
    assert sig.leverage == 100.0


def test_trailing_config_is_ignored_for_tp_extraction():
    text = """#BANK/USDT
SIGNAL Type: Regular (LONG)
Leverage: Cross (50X)
Amount: 5%

Entry Targets:
1) 0.1100
2) 0.1065

Take-Profit Targets:
1) 0.1160
2) 0.1200
3) 0.1400

Stop Target:
1) 0.1035

Trailing Configuration:
Stop: Breakeven -
  Trigger: Target (1)"""

    sig = parse_signal(text)

    assert sig is not None
    assert sig.take_profits == [0.116, 0.12, 0.14]
    assert sig.stop_loss == 0.1035
    assert sig.leverage == 50.0


def test_explicit_stop_loss_wins_over_list_noise():
    text = """#AKE/USDT
SIGNAL Type: Regular (LONG)
Leverage: 10X
Entry Targets:
1) 0.0019
2) 0.0016
Stop Loss: 0.0013
Take-Profit Targets:
1) 0.0025
2) 0.0032
3) 0.0040
"""

    sig = parse_signal(text)

    assert sig is not None
    assert sig.stop_loss == 0.0013
    assert sig.take_profits == [0.0025, 0.0032, 0.004]
    assert sig.leverage == 10.0
