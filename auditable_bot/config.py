from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class BotConfig:
    journal_dir: Path = Path("runtime/auditable_bot/journal")
    stake_usdt: float = 50.0
    fee_rate: float = 0.0006
    slippage_bps: float = 2.0
    max_open_positions: int = 6
    max_entries_per_cycle: int = 3
    daily_loss_limit_usdt: float = 25.0
    min_score: int = 9
    min_discount_pct: float = 3.5
    rsi_max: float = 40.0
    min_qvol: float = 200_000.0
    max_atr_pct: float = 8.0
    max_data_age_sec: int = 660
    true_decay_min_age: float = 60.0
    true_decay_max_mfe_pct: float = 0.25
    true_decay_min_mae_pct: float = -0.80
    cooldown_ms: int = 60 * 60_000
