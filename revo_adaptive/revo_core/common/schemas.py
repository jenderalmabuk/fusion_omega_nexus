from __future__ import annotations

from enum import Enum
from typing import Dict, List, Literal, Optional
from pydantic import BaseModel, Field


class FlowDirection(str, Enum):
    LONG_ONLY = "LONG_ONLY"
    SHORT_ONLY = "SHORT_ONLY"
    BOTH_ALLOWED = "BOTH_ALLOWED"
    NO_TRADE = "NO_TRADE"


class Permission(str, Enum):
    ENTRY_READY = "ENTRY_READY"
    WATCH = "WATCH"
    NO_TRADE = "NO_TRADE"


class Direction(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"


class Blocker(str, Enum):
    STALE_FLOW = "STALE_FLOW"
    STALE_SCANNER = "STALE_SCANNER"
    BTC_PANIC = "BTC_PANIC"
    QVOL_LOW = "QVOL_LOW"
    ATR_EXPLOSIVE = "ATR_EXPLOSIVE"
    FUNDING_CROWDED_LONG = "FUNDING_CROWDED_LONG"
    ER_CHOP = "ER_CHOP"
    FLOW_HOSTILE = "FLOW_HOSTILE"
    SCORE_TOO_LOW = "SCORE_TOO_LOW"
    DISCOUNT_NOT_ENOUGH = "DISCOUNT_NOT_ENOUGH"
    RSI_TOO_HIGH = "RSI_TOO_HIGH"


class PairFlow(BaseModel):
    pair: str
    flow_direction: FlowDirection = FlowDirection.NO_TRADE
    cvd_delta_15m: float = 0.0
    cvd_zscore_15m: float = 0.0
    oi_delta_pct_15m: float = 0.0
    funding_rate: float = 0.0
    funding_zscore: float = 0.0
    volume_zscore_15m: float = 0.0
    data_ready: bool = False
    data_stale: bool = True
    source: Literal["real", "proxy", "mock", "none"] = "none"


class PairRegime(BaseModel):
    pair_regime: str = "unknown"
    efficiency_ratio_48: float = 0.0
    atr_pct: float = 0.0
    volatility_state: str = "unknown"
    funding_state: str = "neutral"
    risk_modifier: float = 1.0


class PairCandidate(BaseModel):
    pair: str
    permission: Permission
    direction: Direction = Direction.LONG
    score: int = 0
    dynamic_min_score: int = 8
    stake_modifier: float = 1.0
    reasons: List[str] = Field(default_factory=list)
    blockers: List[Blocker] = Field(default_factory=list)


class CandidateContext(BaseModel):
    timestamp: str
    profile: str = "balanced_v1"
    pairs: Dict[str, PairCandidate]
    summary: Dict[str, int | float | str] = Field(default_factory=dict)
