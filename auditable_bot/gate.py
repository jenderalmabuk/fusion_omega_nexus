from __future__ import annotations

from .config import BotConfig
from .models import Candidate, GateDecision


def decide_gate(candidate: Candidate, cfg: BotConfig) -> GateDecision:
    f = candidate.frame
    reasons: list[str] = []
    if f.data_age_sec > cfg.max_data_age_sec:
        reasons.append("DENY_DATA_STALE")
    if candidate.qvol < cfg.min_qvol:
        reasons.append("DENY_LIQUIDITY")
    if candidate.score < cfg.min_score:
        reasons.append("DENY_SCORE")
    if candidate.discount_pct < cfg.min_discount_pct:
        reasons.append("DENY_NO_DISCOUNT")
    if f.rsi > cfg.rsi_max:
        reasons.append("DENY_RSI")
    if f.atr_pct > cfg.max_atr_pct:
        reasons.append("DENY_ATR_EXPLOSIVE")
    if f.flow == "hostile":
        reasons.append("DENY_FLOW_HOSTILE")
    if f.funding_z > 1.0:
        reasons.append("DENY_FUNDING_CROWDED")
    if f.btc_mode == "hard_dump" and f.btc_coupling != "decoupled_positive":
        reasons.append("DENY_BTC_HARD_DUMP_COUPLED")
    features = {
        "price": f.close,
        "score": candidate.score,
        "discount_pct": round(candidate.discount_pct, 6),
        "qvol": round(candidate.qvol, 6),
        "rsi": f.rsi,
        "atr_pct": f.atr_pct,
        "cvd_z": f.cvd_z,
        "oi_delta_pct": f.oi_delta_pct,
        "funding_z": f.funding_z,
        "flow": f.flow,
        "btc_mode": f.btc_mode,
        "btc_coupling": f.btc_coupling,
    }
    if reasons:
        return GateDecision(candidate.symbol, False, None, candidate.score, tuple(reasons), features)
    return GateDecision(candidate.symbol, True, candidate.side, candidate.score, ("ALLOW_A_GRADE_PULLBACK",), features)
