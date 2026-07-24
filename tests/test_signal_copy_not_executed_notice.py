import asyncio

from signal_copy.executor import SignalExecutor
from signal_copy.signal_schema import ParsedSignal, SignalSide
from signal_copy.telegram_formatter import build_execution_message
from signal_copy.validation_engine import ValidationResult, Verdict


class _Risk:
    def get_current_equity(self):
        return 1000.0

    def compute_position_size(self, **kwargs):
        return {"notional": 100.0, "sl_price": 570.823}

    async def reserve_open_risk(self, *args, **kwargs):
        raise AssertionError("stale signal must not reach gateway/risk reservation")


class _Trader:
    async def submit_open(self, **kwargs):
        raise AssertionError("stale signal must not submit open")


def test_stale_short_gets_not_executed_reason_before_submit():
    sig = ParsedSignal(
        symbol="ZECUSDT",
        side=SignalSide.SHORT,
        entry_low=528.54,
        entry_high=530.77,
        stop_loss=570.823,
        take_profits=[525.897, 523.255],
    )
    result = ValidationResult(
        signal=sig,
        verdict=Verdict.VALID,
        score=84.5,
        metrics_snapshot={"price": 508.57},
    )

    outcome = asyncio.run(SignalExecutor(_Trader(), _Risk()).execute(result, risk_pct=0.01))
    msg = build_execution_message(outcome, sig, result)

    assert not outcome.ok
    assert outcome.reason == "stale — price 508.57 already past TP1 525.897"
    assert "VALID tapi" in msg
    assert "NOT EXECUTED" in msg
    assert "Reason: stale — price 508.57 already past TP1 525.897" in msg
