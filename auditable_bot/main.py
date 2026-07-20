from __future__ import annotations

import argparse
import json
from pathlib import Path

from .backtest import load_feather_cycles, replay_frames
from .config import BotConfig
from .replay import replay_csv
from .runner import run_once
from .telegram import send
from .universe import load_pair_whitelist


def main() -> int:
    p = argparse.ArgumentParser(description="Auditable standalone paper bot")
    p.add_argument("--replay-csv", type=Path)
    p.add_argument("--config", type=Path)
    p.add_argument("--datadir", type=Path)
    p.add_argument("--backtest-feather", action="store_true")
    p.add_argument("--limit-candles", type=int)
    p.add_argument("--state", type=Path, default=Path("runtime/auditable_bot/state.json"))
    p.add_argument("--journal-dir", type=Path, default=Path("runtime/auditable_bot/journal"))
    p.add_argument("--notify", action="store_true")
    args = p.parse_args()
    if args.replay_csv:
        summary = replay_csv(args.replay_csv, BotConfig(journal_dir=args.journal_dir))
    elif args.backtest_feather and args.config and args.datadir:
        pairs = load_pair_whitelist(args.config)
        cycles = load_feather_cycles(pairs, args.datadir, args.limit_candles)
        summary = replay_frames(cycles, BotConfig(journal_dir=args.journal_dir), force_close=True)
    elif args.config and args.datadir:
        summary = run_once(args.config, args.datadir, args.journal_dir, args.state)
    else:
        p.error("use --replay-csv, --backtest-feather with --config/--datadir, or --config with --datadir")
    text = json.dumps(summary, sort_keys=True)
    print(text)
    if args.notify:
        send("AuditableBot replay\n" + text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
