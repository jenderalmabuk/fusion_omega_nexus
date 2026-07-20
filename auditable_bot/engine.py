from __future__ import annotations

from .config import BotConfig
from .exit import decide_exit
from .features import candidate_from_frame
from .gate import decide_gate
from .journal import Journal
from .models import CycleResult, MarketFrame, Position
from .risk import Risk
from .state import StateStore


class AuditableBot:
    def __init__(self, cfg: BotConfig, state: StateStore | None = None):
        self.cfg = cfg
        self.journal = Journal(cfg.journal_dir)
        self.risk = Risk()
        self.state = state
        self.positions, self.realized_pnl_usdt = state.load() if state else ({}, 0.0)
        self.last_price: dict[str, float] = {s: p.entry_price for s, p in self.positions.items()}

    def mark_price(self, symbol: str, price: float) -> None:
        self.last_price[symbol] = price
        if symbol in self.positions:
            self.positions[symbol].update(price)

    def run_cycle(self, frames: list[MarketFrame], now_ms: int) -> CycleResult:
        exits = self._process_exits(now_ms)
        candidates = [candidate_from_frame(f) for f in frames]
        entries = 0
        rejected = 0
        reasons: list[str] = []
        for c in candidates:
            self.mark_price(c.symbol, c.price)
            self.journal.write("candidate_seen", {"candidate": c})
            decision = decide_gate(c, self.cfg)
            extra = self.risk.reasons(decision, self.positions, self.realized_pnl_usdt, now_ms, self.cfg)
            if extra:
                decision = type(decision)(decision.symbol, False, None, decision.score, decision.reasons + extra, decision.features)
            self.journal.write("gate_decision", {"decision": decision, "allow": decision.allow, "reasons": decision.reasons})
            if not decision.allow:
                rejected += 1
                reasons.extend(decision.reasons)
                continue
            if entries >= self.cfg.max_entries_per_cycle:
                rejected += 1
                reasons.append("DENY_MAX_ENTRIES_PER_CYCLE")
                self.journal.write("gate_decision", {"decision": {"symbol": c.symbol, "allow": False, "reasons": ["DENY_MAX_ENTRIES_PER_CYCLE"]}})
                continue
            qty = self.cfg.stake_usdt / c.price
            self.positions[c.symbol] = Position(c.symbol, c.side, c.price, qty, now_ms, c.price, c.price)
            entries += 1
            self.journal.write("paper_trades", {"event": "entry", "symbol": c.symbol, "price": c.price, "qty": qty, "reasons": decision.reasons})
        self._save_state()
        return CycleResult(len(candidates), entries, exits, rejected, tuple(reasons))

    def _save_state(self) -> None:
        if self.state:
            self.state.save(self.positions, self.realized_pnl_usdt)

    def _process_exits(self, now_ms: int) -> int:
        closed = 0
        for symbol, pos in list(self.positions.items()):
            price = self.last_price.get(symbol, pos.entry_price)
            pos.update(price)
            decision = decide_exit(pos, price, now_ms, self.cfg)
            self.journal.write("exit_decision", {"decision": decision, "reason": decision.reason, "exit": decision.exit})
            self.journal.write("position_snapshots", {"position": pos, "price": price})
            if not decision.exit:
                continue
            self.realized_pnl_usdt += decision.net_pnl_usdt
            self.risk.cool(symbol, now_ms, self.cfg)
            del self.positions[symbol]
            closed += 1
            self.journal.write("paper_trades", {"event": "exit", "symbol": symbol, "price": price, "pnl_usdt": decision.net_pnl_usdt, "reason": decision.reason})
        return closed
