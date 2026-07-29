from signal_copy.listeners.telegram_listener import TelegramSignalListener


def test_reply_context_is_symbol_only_not_full_signal():
    listener = TelegramSignalListener(lambda *args: None)
    text = listener._reply_context_marker(
        "#ONDO/USDT Take-Profit target 1\nDirection: LONG\nEntry: 0.3825 - 0.3840\nStop Loss: 0.35328"
    )
    assert text == "[REPLY_SYMBOL: ONDOUSDT]"


def test_reply_context_without_symbol_is_ignored():
    listener = TelegramSignalListener(lambda *args: None)
    assert listener._reply_context_marker("Move SL to BEP") == ""
