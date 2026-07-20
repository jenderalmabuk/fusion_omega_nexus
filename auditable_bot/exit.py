from __future__ import annotations

from .config import BotConfig
from .models import ExitDecision, Position


def estimate_net_pnl(pos: Position, price: float, cfg: BotConfig) -> float:
    gross = (price - pos.entry_price) * pos.qty
    fee = (pos.entry_price * pos.qty + price * pos.qty) * cfg.fee_rate
    slip = (pos.entry_price * pos.qty + price * pos.qty) * (cfg.slippage_bps / 10_000)
    return gross - fee - slip


def decide_exit(pos: Position, price: float, now_ms: int, cfg: BotConfig) -> ExitDecision:
    age_min = (now_ms - pos.opened_ms) / 60_000
    net = estimate_net_pnl(pos, price, cfg)
    features = {
        "age_min": round(age_min, 4),
        "mfe_pct": round(pos.mfe_pct(), 6),
        "mae_pct": round(pos.mae_pct(), 6),
        "net_pnl_usdt": round(net, 6),
        "partial_done": pos.partial_done,
        "thesis_valid": pos.thesis_valid,
    }
    if not pos.thesis_valid:
        return ExitDecision(pos.symbol, True, "EXIT_THESIS_INVALIDATED", net, features)
    true_decay = (
        age_min >= cfg.true_decay_min_age
        and pos.mfe_pct() <= cfg.true_decay_max_mfe_pct
        and pos.mae_pct() <= cfg.true_decay_min_mae_pct
        and net < 0
        and not pos.partial_done
    )
    if true_decay:
        return ExitDecision(pos.symbol, True, "EXIT_TRUE_DECAY", net, features)
    if age_min >= cfg.true_decay_min_age and pos.mfe_pct() > cfg.true_decay_max_mfe_pct:
        return ExitDecision(pos.symbol, False, "HOLD_RECOVERY_SAFE", net, features)
    return ExitDecision(pos.symbol, False, "HOLD_BASELINE", net, features)
