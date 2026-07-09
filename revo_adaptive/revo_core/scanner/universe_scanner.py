from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List


@dataclass(frozen=True)
class UniversePair:
    pair: str
    quote_volume: float = 0.0
    price_change_pct: float = 0.0


def sort_universe(pairs: Iterable[UniversePair], min_volume: float = 600_000.0, limit: int | None = None) -> List[UniversePair]:
    """Filter by volume and sort by absolute 24h price change.

    This mirrors the user's preferred scanner behavior: broad universe first,
    min volume threshold, no arbitrary cap except downstream Freqtrade limit.
    """
    out = [p for p in pairs if p.quote_volume >= min_volume]
    out.sort(key=lambda p: abs(p.price_change_pct), reverse=True)
    return out[:limit] if limit else out
