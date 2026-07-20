"""Regression for the real channel formats audited in the multi-channel batch.

Each case is a verbatim sample the user forwarded; the parser must extract the
right symbol/side/entry/SL/TP without false positives.
"""
from signal_copy.signal_parser import parse_signal


def test_bank_long_trade_setup_spaced_targets():
    sig = parse_signal("""🟢BANK/USDT My Long Trade Setup

Leverage 50X

Entry   :  0.05760

Targets  :  0.05800  0.058500  0.05900

Stop Loss  :  0.05700""")
    assert sig is not None
    assert sig.symbol == "BANKUSDT"
    assert sig.side.value == "LONG"
    assert sig.entry_low == 0.0576
    assert sig.stop_loss == 0.057
    assert sig.take_profits == [0.058, 0.0585, 0.059]
    assert sig.leverage == 50


def test_mtc_indicator_short_multiline_tps():
    sig = parse_signal("""🔥 MTC Indicator 🔥

🔴 SHORT

#T/USDT

Entry zone : 0.0047163 - 0.0045790

Take Profits : 

0.0045558
0.0044178
0.0042797

Stop loss :0.00489953

Leverage: 10x""")
    assert sig is not None
    assert sig.symbol == "TUSDT"
    assert sig.side.value == "SHORT"
    assert sig.stop_loss == 0.00489953
    assert sig.take_profits == [0.0045558, 0.0044178, 0.0042797]


def test_bank_entry_targets_with_keycap_emoji_tps():
    sig = parse_signal("""🚨 #BANK/USDT | LONG

⚡Leverage: Cross 50x

🎯Entry Targets: 0.07100

💵Take Profits👇

1️⃣0.07300

2️⃣0.07550

3️⃣0.07800

4️⃣0.08200

🛑Stop Loss: 0.06200""")
    assert sig is not None
    assert sig.symbol == "BANKUSDT"
    assert sig.side.value == "LONG"
    assert sig.entry_low == 0.071
    assert sig.stop_loss == 0.062
    assert sig.take_profits == [0.073, 0.0755, 0.078, 0.082]
    assert sig.leverage == 50


def test_eth_market_setup_side_inferred_from_geometry():
    # No LONG/SHORT word; SL(1845) > entry(1820) => SHORT inferred.
    sig = parse_signal("""😀😀😀😀 ETH

🔖MARKET SETUP

 ✔️Entry: 1820
 ⛔️ SL: 1845
 💵 TP: 1792""")
    assert sig is not None
    assert sig.symbol == "ETHUSDT"
    assert sig.side.value == "SHORT"
    assert sig.entry_low == 1820.0
    assert sig.stop_loss == 1845.0
    assert sig.take_profits == [1792.0]


def test_bulla_hashtag_below_entry_side_inferred_from_targets():
    # "#BULLA" bare hashtag, "Entry: Below 0.009", no side word; targets above
    # entry => LONG inferred.
    sig = parse_signal("""🔥#BULLA (Binance Futures)🔥

Leverage: 3-5x

Entry: Below 0.009

Targets: 0.0092 - 0.0094 - 0.0096 - OPEN

SL: Below 0.0068""")
    assert sig is not None
    assert sig.symbol == "BULLAUSDT"
    assert sig.side.value == "LONG"
    assert sig.entry_low == 0.009
    assert sig.leverage == 5


def test_tinker_coin_name_not_misread_as_symbol():
    # "Coin Name: HOMEUSDT" must yield HOME, not NAME.
    sig = parse_signal("""🏦Tinker Alert:~ Future Signal

0️⃣Coin Name: HOMEUSDT

🔼Direction: LONG 25x🔼

📢 Valid Entry: 0.00681

💰 Targets: 0.00686 _ 0.00691 _ 0.00694

❗️Stoploss: 0.00661 ❗️""")
    assert sig is not None
    assert sig.symbol == "HOMEUSDT"
    assert sig.side.value == "LONG"
    assert sig.entry_low == 0.00681
    assert sig.stop_loss == 0.00661
    assert sig.leverage == 25


def test_dodox_leading_entry_reversed_symbol_line():
    sig = parse_signal("""✅Entry 0.02720 - 0.026354

(LONG DODOXUSDT 5-20x🆘)

🏹 Target 1 $0.027506
🏹Target 2. $0.027775
🏹Target 3  $0.028065

🚫Stop loss -0.02573""")
    assert sig is not None
    assert sig.symbol == "DODOXUSDT"
    assert sig.side.value == "LONG"
    assert sig.stop_loss == 0.02573
    assert sig.take_profits == [0.027506, 0.027775, 0.028065]
