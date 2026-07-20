from __future__ import annotations

import time
from pathlib import Path

from .config import BotConfig
from .datasource import latest_from_feather
from .engine import AuditableBot
from .report import summarize_journal
from .state import StateStore
from .universe import load_pair_whitelist


def run_once(config_path: Path, datadir: Path, journal_dir: Path, state_path: Path, now_ms: int | None = None) -> dict:
    cfg = BotConfig(journal_dir=journal_dir)
    bot = AuditableBot(cfg, StateStore(state_path))
    frames = []
    for pair in load_pair_whitelist(config_path):
        try:
            frames.append(latest_from_feather(pair, datadir))
        except (FileNotFoundError, ValueError, KeyError):
            continue
    result = bot.run_cycle(frames, now_ms if now_ms is not None else int(time.time() * 1000))
    summary = summarize_journal(journal_dir)
    return {**summary, "candidates": result.candidates, "entries_this_cycle": result.entries, "exits_this_cycle": result.exits}
