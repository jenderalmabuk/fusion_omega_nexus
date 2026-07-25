import asyncio

from signal_copy.executor import SignalExecutor
from signal_copy.signal_schema import ParsedSignal, SignalSide
from signal_copy.telegram_formatter import build_execution_message
from signal_copy.validation_engine import ValidationResult, Verdict


class _Risk:
    def get_current_equity(self): return 1000.0
    def compute_position_size(self, **kwargs): return {"notional": 100.0, "sl_price": 570.823}
    async def reserve_open_risk(self, *args, **kwargs):
        raise AssertionError("stale signal must not reserve risk")


class _Trader:
    async def submit_open(self, **kwargs):
        raise AssertionError("stale signal must not submit")


def test_short_with_all_targets_passed_gets_reason_before_submit():
    sig = ParsedSignal("ZECUSDT", SignalSide.SHORT, 528.54, 530.77,
                       stop_loss=570.823, take_profits=[525.897, 523.255])
    result = ValidationResult(signal=sig, verdict=Verdict.VALID, score=84.5,
                              metrics_snapshot={"price": 520.0})
    outcome = asyncio.run(SignalExecutor(_Trader(), _Risk()).execute(result, risk_pct=0.01))
    msg = build_execution_message(outcome, sig, result)
    assert not outcome.ok
    assert outcome.reason == "ALL_TARGETS_PASSED — price 520"
    assert "VALID tapi" in msg and "NOT EXECUTED" in msg


def test_short_past_tp1_but_not_full_target_still_submits():
    class Risk(_Risk):
        async def reserve_open_risk(self, *args, **kwargs): return True
        async def commit_open_trade(self, *args, **kwargs): return None
    class Trader:
        async def submit_open(self, **kwargs):
            return {"entry_price": 2.05, "notional": kwargs["notional"]}
    sig = ParsedSignal("NEOUSDT", SignalSide.SHORT, 2.068, 2.130,
                       stop_loss=2.21276,
                       take_profits=[2.057, 1.995, 1.932, 1.870, 1.808])
    result = ValidationResult(signal=sig, verdict=Verdict.VALID, score=80,
                              metrics_snapshot={"price": 2.05})
    outcome = asyncio.run(SignalExecutor(Trader(), Risk()).execute(result, risk_pct=0.01))
    assert outcome.ok
