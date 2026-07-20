from __future__ import annotations

from .models import Candidate, MarketFrame


def discount_pct(frame: MarketFrame) -> float:
    return max(0.0, (frame.ema55 - frame.close) / frame.ema55 * 100.0) if frame.ema55 else 0.0


def qvol(frame: MarketFrame) -> float:
    return frame.close * frame.volume


def score(frame: MarketFrame) -> int:
    s = 0
    s += 3 if discount_pct(frame) >= 3.5 else 0
    s += 3 if frame.rsi <= 40 else 0
    s += 2 if qvol(frame) >= 200_000 else 0
    s += 1 if frame.atr_pct <= 8 else 0
    s += 1 if frame.cvd_z > -0.5 else 0
    s += 1 if abs(frame.oi_delta_pct) > 0 else 0
    s += 1 if frame.funding_z <= 1.0 else 0
    return s


def candidate_from_frame(frame: MarketFrame) -> Candidate:
    return Candidate(frame.symbol, "long", frame.close, score(frame), discount_pct(frame), qvol(frame), frame)
