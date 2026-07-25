from signal_copy.telegram_formatter import build_close_message


def test_fusion_quantum_close_footer_override():
    msg = build_close_message({
        "symbol": "BTCUSDT", "side": "LONG", "footer": "Fusion Quantum Dry-Run",
    })
    assert msg.endswith("Fusion Quantum Dry-Run")
    assert "Fusion Signal Copy" not in msg
