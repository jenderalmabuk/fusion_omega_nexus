from __future__ import annotations


def classify_funding(funding_rate: float, zscore: float | None = None) -> str:
    """Classify funding for long-side contrarian logic."""
    if zscore is not None:
        if zscore <= -1.0:
            return 'contrarian_long'
        if zscore >= 1.0:
            return 'crowded_long'
        return 'neutral'
    if funding_rate <= 0:
        return 'contrarian_long'
    if funding_rate >= 0.0003:
        return 'crowded_long'
    return 'neutral'
