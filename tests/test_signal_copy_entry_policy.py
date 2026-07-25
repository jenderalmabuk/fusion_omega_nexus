from signal_copy.entry_policy import (
    EntryAction,
    PricePath,
    route_entry,
    revalidate_pending,
)
from signal_copy.signal_schema import ParsedSignal, SignalSide


def neo() -> ParsedSignal:
    return ParsedSignal(
        symbol="NEOUSDT",
        side=SignalSide.SHORT,
        entry_low=2.068,
        entry_high=2.130,
        stop_loss=2.21276,
        take_profits=[2.057, 1.995, 1.932, 1.870, 1.808],
        leverage=10,
    )


def test_short_inside_zone_routes_market_without_rewriting_provider_levels():
    sig = neo()
    decision = route_entry(sig, 2.100)
    assert decision.action == EntryAction.MARKET
    assert decision.entry == 2.100
    assert sig.stop_loss == 2.21276
    assert sig.take_profits == [2.057, 1.995, 1.932, 1.870, 1.808]


def test_short_above_zone_waits_at_nearest_boundary():
    decision = route_entry(neo(), 2.150)
    assert decision.action == EntryAction.LIMIT
    assert decision.entry == 2.130
    assert decision.code == "PRICE_OUTSIDE_ENTRY_ZONE"


def test_short_below_zone_even_past_tp1_waits_for_retest():
    decision = route_entry(neo(), 2.050)
    assert decision.action == EntryAction.LIMIT
    assert decision.entry == 2.068
    assert decision.passed_targets == [2.057]


def test_all_targets_passed_rejects():
    decision = route_entry(neo(), 1.800)
    assert decision.action == EntryAction.REJECT
    assert decision.code == "ALL_TARGETS_PASSED"


def test_sl_passed_rejects():
    decision = route_entry(neo(), 2.220)
    assert decision.action == EntryAction.REJECT
    assert decision.code == "SL_ALREADY_PASSED"


def test_effective_one_r_target_reached_before_fill_permanently_cancels():
    # TP3 is first target >=1R from zone midpoint.
    result = revalidate_pending(neo(), PricePath(high=2.150, low=1.930), current_price=2.068)
    assert not result.ok
    assert result.code == "THESIS_TARGET_REACHED_BEFORE_ENTRY"


def test_tp1_excursion_does_not_kill_pending_signal():
    result = revalidate_pending(neo(), PricePath(high=2.150, low=2.050), current_price=2.068)
    assert result.ok
    assert result.code == "THESIS_VALID"


def test_stop_touched_before_fill_permanently_cancels():
    result = revalidate_pending(neo(), PricePath(high=2.220, low=2.068), current_price=2.068)
    assert not result.ok
    assert result.code == "STOP_INVALIDATED_BEFORE_ENTRY"


def test_market_conflicts_require_three_independent_categories():
    path = PricePath(high=2.150, low=2.050)
    assert revalidate_pending(neo(), path, 2.068, conflicts={"flow", "rsi"}).ok
    result = revalidate_pending(neo(), path, 2.068, conflicts={"structure", "htf_regime", "flow"})
    assert not result.ok
    assert result.code == "MARKET_THESIS_INVALIDATED"


def test_rr_ladder_uses_candidate_fill_not_old_midpoint():
    decision = route_entry(neo(), 2.100)
    assert round(decision.rr_by_target[0], 2) == 0.38
    assert round(decision.rr_by_target[2], 2) == 1.49
