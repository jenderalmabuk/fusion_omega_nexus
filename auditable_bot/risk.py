from __future__ import annotations

from .config import BotConfig
from .models import GateDecision, Position


class Risk:
    def __init__(self):
        self.cooldown_until: dict[str, int] = {}

    def reasons(self, decision: GateDecision, positions: dict[str, Position], realized_pnl: float, now_ms: int, cfg: BotConfig) -> tuple[str, ...]:
        out: list[str] = []
        if decision.symbol in positions:
            out.append("DENY_DUPLICATE_POSITION")
        if len(positions) >= cfg.max_open_positions:
            out.append("DENY_MAX_OPEN_POSITIONS")
        if realized_pnl <= -abs(cfg.daily_loss_limit_usdt):
            out.append("DENY_DAILY_LOSS_LIMIT")
        if now_ms < self.cooldown_until.get(decision.symbol, 0):
            out.append("DENY_PAIR_COOLDOWN")
        return tuple(out)

    def cool(self, symbol: str, now_ms: int, cfg: BotConfig) -> None:
        self.cooldown_until[symbol] = now_ms + cfg.cooldown_ms
