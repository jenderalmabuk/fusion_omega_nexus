from signal_copy.signal_parser import parse_signal


def test_scalping300_em_dash_entry_range_and_target_ladder():
    sig = parse_signal("""꧁༺ 𝓢𝓒𝓐𝓛𝓟𝓘𝓝𝓖 300 ༻꧂
ONDOUSDT

Direction: SHORT
Leverage: Cross 20x
★ Entry: 0.3418 — 0.3421 ★

🔥Stop Loss: 0.369144🔥

Take Profits:
Target 1 - 0.340091
Target 2 - 0.338382
Target 3 - 0.334964
Target 4 - 0.331546
Target 5 - 0.328128
Target 6 - 0.32471
Target 7 - 0.321292
Target 8 - 0.317874""")

    assert sig is not None
    assert sig.symbol == "ONDOUSDT"
    assert sig.side.value == "SHORT"
    assert sig.entry_low == 0.3418
    assert sig.entry_high == 0.3421
    assert sig.stop_loss == 0.369144
    assert sig.take_profits == [0.340091, 0.338382, 0.334964, 0.331546, 0.328128, 0.32471, 0.321292, 0.317874]
    assert sig.leverage == 20
