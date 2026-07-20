from signal_copy.signal_parser import parse_signal


def test_coin_hashtag_entries_and_multiply_leverage_range():
    sig = parse_signal("""Coin #ESPORTS/USDT

Position: LONG

Leverage:  Cross 10×  To 50×

Entries:  0.0450 - 0.0435

Targets: 🎯 0.0465, 0.0490, 0.0535

Stop Loss: 0.0420

📌 Published by @Liam_Ricardo1""")

    assert sig is not None
    assert sig.symbol == "ESPORTSUSDT"
    assert sig.side.value == "LONG"
    assert sig.entry_low == 0.0435
    assert sig.entry_high == 0.045
    assert sig.stop_loss == 0.042
    assert sig.take_profits == [0.0465, 0.049, 0.0535]
    assert sig.leverage == 50
