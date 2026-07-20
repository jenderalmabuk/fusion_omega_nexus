from signal_copy.signal_parser import parse_signal


def test_quick_pump_unicode_entry_tail_noise():
    sig = parse_signal("""📊#TOSHI/𝐔𝐒𝐃𝐓 – 𝐋𝐎𝐍𝐆–𝟑𝟎𝐗 🚀
📍 𝐄𝐧𝐭𝐫𝐲 𝐙𝐨𝐧𝐞:
➥ 0.000129, 0.000

🎯𝐓𝐚𝐤𝐞 𝐏𝐫𝐨𝐟𝐢𝐭:
✅️ TP1 0.000133
✅️ TP2 0.000138
✅️ TP2 0.000150

🛑 𝐒𝐭𝐨𝐩 𝐋𝐨𝐬𝐬: - 0.000118""")

    assert sig is not None
    assert sig.symbol == "TOSHIUSDT"
    assert sig.side.value == "LONG"
    assert sig.entry_low == sig.entry_high == 0.000129
    assert sig.stop_loss == 0.000118
    assert sig.take_profits == [0.000133, 0.000138, 0.00015]
    assert sig.leverage == 30


def test_crypto_trades_numbered_entry_price_and_leverage_range():
    sig = parse_signal("""💎 #Free Signal
🔴 Short

Pair: #BTC/USDT

📊 Entry Price:

1) 64494.70
2) 66429.54

📈 Targets:

1) 64116.94
2) 62755.51
3) 61394.07
4) 60032.64

Stop Loss: 68557.86

Leverage: 10x-20x""")

    assert sig is not None
    assert sig.symbol == "BTCUSDT"
    assert sig.side.value == "SHORT"
    assert sig.entry_low == 64494.70
    assert sig.entry_high == 66429.54
    assert sig.stop_loss == 68557.86
    assert sig.take_profits == [64116.94, 62755.51, 61394.07, 60032.64]
    assert sig.leverage == 20
