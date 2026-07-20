from signal_copy.signal_parser import parse_signal


def test_trade_setup_with_thousands_commas_parses_as_price_not_decimal():
    text = """🟢$ETH/USDT My Short Trade
      Setup

      Entry  -    1,730
      Leverage 100X
      Target -  1,690
       SL  -     1,744"""

    sig = parse_signal(text)

    assert sig is not None
    assert sig.symbol == "ETHUSDT"
    assert sig.side.value == "SHORT"
    assert sig.entry_low == 1730.0
    assert sig.entry_high == 1730.0
    assert sig.take_profits == [1690.0]
    assert sig.stop_loss == 1744.0
    assert sig.leverage == 100.0


def test_trade_setup_with_decimal_prices_keeps_decimals():
    text = """🟢$NEAR/USDT My Long Trade
      Setup

      Entry  -    1.905
      Leverage 50X
      Target -  1.961
       SL  -     1.888"""

    sig = parse_signal(text)

    assert sig is not None
    assert sig.symbol == "NEARUSDT"
    assert sig.side.value == "LONG"
    assert sig.entry_low == 1.905
    assert sig.entry_high == 1.905
    assert sig.take_profits == [1.961]
    assert sig.stop_loss == 1.888
    assert sig.leverage == 50.0
