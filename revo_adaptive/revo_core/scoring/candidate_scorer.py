from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict

from revo_core.common.schemas import CandidateContext, Direction, PairCandidate, Permission, Blocker


def score_permission(score: int, min_score: int, blockers: list[Blocker]) -> Permission:
    hard = {Blocker.STALE_FLOW, Blocker.BTC_PANIC, Blocker.ATR_EXPLOSIVE, Blocker.FLOW_HOSTILE}
    if any(b in hard for b in blockers):
        return Permission.NO_TRADE
    if score >= min_score and not blockers:
        return Permission.ENTRY_READY
    return Permission.WATCH


def make_context(candidates: Dict[str, PairCandidate], profile: str = 'balanced_v1') -> CandidateContext:
    summary = {
        'total': len(candidates),
        'entry_ready': sum(1 for c in candidates.values() if c.permission == Permission.ENTRY_READY),
        'watch': sum(1 for c in candidates.values() if c.permission == Permission.WATCH),
        'no_trade': sum(1 for c in candidates.values() if c.permission == Permission.NO_TRADE),
    }
    return CandidateContext(
        timestamp=datetime.now(timezone.utc).isoformat(),
        profile=profile,
        pairs=candidates,
        summary=summary,
    )


def candidate_from_score(pair: str, score: int, min_score: int, blockers: list[Blocker] | None = None) -> PairCandidate:
    blockers = blockers or []
    return PairCandidate(
        pair=pair,
        direction=Direction.LONG,
        score=score,
        dynamic_min_score=min_score,
        stake_modifier=1.0,
        blockers=blockers,
        reasons=[],
        permission=score_permission(score, min_score, blockers),
    )
