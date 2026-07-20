from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class MarketFrame:
    symbol: str
    close: float
    volume: float
    rsi: float
    ema55: float
    atr_pct: float
    cvd_z: float
    oi_delta_pct: float
    funding_z: float
    flow: str
    btc_mode: str
    btc_coupling: str
    data_age_sec: int


@dataclass(frozen=True)
class Candidate:
    symbol: str
    side: str
    price: float
    score: int
    discount_pct: float
    qvol: float
    frame: MarketFrame


@dataclass(frozen=True)
class GateDecision:
    symbol: str
    allow: bool
    side: str | None
    score: int
    reasons: tuple[str, ...]
    features: dict


@dataclass
class Position:
    symbol: str
    side: str
    entry_price: float
    qty: float
    opened_ms: int
    max_price: float
    min_price: float
    partial_done: bool = False
    thesis_valid: bool = True

    def update(self, price: float) -> None:
        self.max_price = max(self.max_price, price)
        self.min_price = min(self.min_price, price)

    def mfe_pct(self) -> float:
        return (self.max_price / self.entry_price - 1.0) * 100.0

    def mae_pct(self) -> float:
        return (self.min_price / self.entry_price - 1.0) * 100.0


@dataclass(frozen=True)
class ExitDecision:
    symbol: str
    exit: bool
    reason: str
    net_pnl_usdt: float
    features: dict


@dataclass(frozen=True)
class CycleResult:
    candidates: int = 0
    entries: int = 0
    exits: int = 0
    rejected: int = 0
    reasons: tuple[str, ...] = field(default_factory=tuple)
