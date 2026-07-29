from signal_copy.provider_updates import UpdateKind, parse_provider_update, safer_stop


def test_english_bep_variants():
    for text in ("Move SL to BEP", "move sl to breakeven", "SL moved to BE"):
        u = parse_provider_update(text, -1001652601224, reply_symbol="NEOUSDT")
        assert u and u.kind == UpdateKind.MOVE_SL_BE and u.symbol == "NEOUSDT"


def test_explicit_stop_price():
    u = parse_provider_update("NEO SL move to 2.080", -1001652601224)
    assert u and u.kind == UpdateKind.MOVE_SL_PRICE
    assert u.symbol == "NEOUSDT" and u.price == 2.080


def test_cancel_and_close():
    assert parse_provider_update("Cancel NEO signal", -1001652601224).kind == UpdateKind.CANCEL
    assert parse_provider_update("Close NEO now", -1001652601224).kind == UpdateKind.CLOSE


def test_other_channels_disabled():
    assert parse_provider_update("Move SL to BEP", -1001727857237, reply_symbol="NEOUSDT") is None


def test_ambiguous_message_without_symbol_or_reply_rejected():
    assert parse_provider_update("Move SL to BEP", -1001652601224) is None


def test_generic_words_never_become_symbols():
    samples = (
        "CRYPTO signal close now",
        "Close full position",
        "I full closed it",
        "FULL SETUP DETAILS IS HERE close now",
        "Close all trade now",
    )
    for text in samples:
        assert parse_provider_update(text, -1001652601224) is None, text


def test_reply_context_allows_generic_close_update():
    u = parse_provider_update("Close full position", -1001652601224,
                              reply_symbol="ETHUSDT")
    assert u and u.kind == UpdateKind.CLOSE and u.symbol == "ETHUSDT"


def test_only_risk_reducing_stop_is_safe():
    assert safer_stop("LONG", old_sl=95, new_sl=100, entry=105)
    assert not safer_stop("LONG", old_sl=95, new_sl=90, entry=105)
    assert safer_stop("SHORT", old_sl=110, new_sl=105, entry=100)
    assert not safer_stop("SHORT", old_sl=110, new_sl=115, entry=100)


def test_tp_hit_classification():
    u = parse_provider_update("NEO TP1 hit", -1001652601224)
    assert u and u.kind == UpdateKind.TP_HIT and u.tp_index == 1
