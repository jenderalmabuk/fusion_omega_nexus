from __future__ import annotations

import csv
from pathlib import Path

from .config import BotConfig
from .engine import AuditableBot
from .models import MarketFrame


def load_csv(path: Path) -> list[MarketFrame]:
    rows: list[MarketFrame] = []
    with Path(path).open(newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            rows.append(MarketFrame(
                r["symbol"], float(r["close"]), float(r["volume"]), float(r["rsi"]), float(r["ema55"]),
                float(r["atr_pct"]), float(r["cvd_z"]), float(r["oi_delta_pct"]), float(r["funding_z"]),
                r["flow"], r["btc_mode"], r["btc_coupling"], int(float(r["data_age_sec"])),
            ))
    return rows


def replay_csv(path: Path, cfg: BotConfig) -> dict:
    bot = AuditableBot(cfg)
    frames = load_csv(path)
    total = {"cycles": 0, "candidates": 0, "entries": 0, "exits": 0, "rejected": 0, "realized_pnl_usdt": 0.0}
    for i, frame in enumerate(frames):
        res = bot.run_cycle([frame], now_ms=i * 5 * 60_000)
        total["cycles"] += 1
        total["candidates"] += res.candidates
        total["entries"] += res.entries
        total["exits"] += res.exits
        total["rejected"] += res.rejected
    bot.run_cycle([], now_ms=(len(frames) + 24) * 5 * 60_000)
    total["realized_pnl_usdt"] = round(bot.realized_pnl_usdt, 6)
    total["open_positions"] = len(bot.positions)
    return total
