from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class Config:
    min_score: int = 9
    min_discount_pct: float = 3.5
    rsi_max: float = 40.0
    min_qvol_med48: float = 200_000.0
    max_atr_pct: float = 8.0
    max_data_age_sec: int = 120
    true_decay_min_age: float = 60.0
    true_decay_max_mfe_pct: float = 0.25
    true_decay_min_mae_pct: float = -0.80


@dataclass(frozen=True)
class GateInput:
    symbol: str
    score: float
    rsi: float
    discount_pct: float
    qvol_med48: float
    atr_pct: float
    flow: str
    btc_mode: str
    btc_coupling: str
    data_age_sec: int


@dataclass(frozen=True)
class GateDecision:
    allow: bool
    side: str | None
    grade: str
    score: float
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class Position:
    symbol: str
    side: str
    entry_price: float
    age_min: float
    mfe_pct: float
    mae_pct: float
    partial_done: bool
    thesis_valid: bool
    net_pnl_pct: float


@dataclass(frozen=True)
class ExitDecision:
    exit: bool
    reason: str


@dataclass(frozen=True)
class Candle:
    close: float
    volume: float
    rsi: float
    ema55: float
    atr_pct: float


def decide_entry(g: GateInput, c: Config = Config()) -> GateDecision:
    reasons: list[str] = []
    if g.data_age_sec > c.max_data_age_sec:
        reasons.append("DENY_DATA_STALE")
    if g.qvol_med48 < c.min_qvol_med48:
        reasons.append("DENY_LIQUIDITY")
    if g.score < c.min_score:
        reasons.append("DENY_SCORE")
    if g.rsi > c.rsi_max:
        reasons.append("DENY_RSI")
    if g.discount_pct < c.min_discount_pct:
        reasons.append("DENY_NO_DISCOUNT")
    if g.atr_pct > c.max_atr_pct:
        reasons.append("DENY_ATR_EXPLOSIVE")
    if g.flow not in {"long", "neutral", "unknown"}:
        reasons.append("DENY_FLOW_HOSTILE")
    if g.btc_mode == "hard_dump" and g.btc_coupling != "decoupled_positive":
        reasons.append("DENY_BTC_HARD_DUMP_COUPLED")
    if reasons:
        return GateDecision(False, None, "F", g.score, tuple(reasons))
    return GateDecision(True, "long", "A", g.score, ("ALLOW_A_GRADE_PULLBACK",))


def decide_exit(p: Position, c: Config = Config()) -> ExitDecision:
    if not p.thesis_valid:
        return ExitDecision(True, "EXIT_THESIS_INVALIDATED")
    if p.partial_done:
        return ExitDecision(False, "HOLD_PARTIAL_DONE")
    true_decay = (
        p.age_min >= c.true_decay_min_age
        and p.mfe_pct <= c.true_decay_max_mfe_pct
        and p.mae_pct <= c.true_decay_min_mae_pct
        and p.net_pnl_pct < 0
    )
    if true_decay:
        return ExitDecision(True, "EXIT_TRUE_DECAY")
    if p.age_min >= c.true_decay_min_age and p.mfe_pct > c.true_decay_max_mfe_pct:
        return ExitDecision(False, "HOLD_RECOVERY_SAFE")
    return ExitDecision(False, "HOLD_BASELINE")


def append_jsonl(path: str | Path, row: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"ts": datetime.now(timezone.utc).isoformat(), **row}
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n")


def candle_to_gate(symbol: str, candle: Candle, flow: str, btc_mode: str, data_age_sec: int = 0) -> GateInput:
    discount = max(0.0, (candle.ema55 - candle.close) / candle.ema55 * 100.0) if candle.ema55 else 0.0
    score = 0
    score += 3 if discount >= 3.5 else 0
    score += 3 if candle.rsi <= 40 else 0
    score += 2 if candle.close * candle.volume >= 200_000 else 0
    score += 1 if candle.atr_pct <= 8 else 0
    return GateInput(symbol, score, candle.rsi, discount, candle.close * candle.volume, candle.atr_pct, flow, btc_mode, "coupled", data_age_sec)


def replay_candidates(symbol: str, candles: Iterable[Candle], flow: str, btc_mode: str, c: Config = Config()) -> Iterable[GateDecision]:
    for candle in candles:
        yield decide_entry(candle_to_gate(symbol, candle, flow, btc_mode), c)


def decision_row(event: str, obj) -> dict:
    return {"event": event, **asdict(obj)}

