from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from .config import BotConfig
from .datasource import freqtrade_pair_to_file_stem, market_frame_from_row
from .engine import AuditableBot
from .exit import estimate_net_pnl
from .models import MarketFrame
from .report import summarize_journal


def replay_frames(cycles: Iterable[tuple[int, list[MarketFrame]]], cfg: BotConfig, force_close: bool = False) -> dict:
    bot = AuditableBot(cfg)
    peak = trough = 0.0
    forced = 0
    for now_ms, frames in cycles:
        for frame in frames:
            bot.mark_price(frame.symbol, frame.close)
        bot.run_cycle(frames, now_ms)
        equity = bot.realized_pnl_usdt
        peak = max(peak, equity)
        trough = min(trough, equity)
    if force_close:
        for symbol, pos in list(bot.positions.items()):
            price = bot.last_price.get(symbol, pos.entry_price)
            pnl = estimate_net_pnl(pos, price, cfg)
            bot.realized_pnl_usdt += pnl
            bot.journal.write("paper_trades", {"event": "exit", "symbol": symbol, "price": price, "pnl_usdt": pnl, "reason": "EXIT_FORCED_BACKTEST_END"})
            del bot.positions[symbol]
            forced += 1
    summary = summarize_journal(cfg.journal_dir)
    pnls = [float(r.get("pnl_usdt", 0.0)) for r in _trade_rows(cfg.journal_dir) if r.get("event") == "exit"]
    wins = sum(1 for x in pnls if x > 0)
    losses = sum(1 for x in pnls if x < 0)
    gross_win = sum(x for x in pnls if x > 0)
    gross_loss = abs(sum(x for x in pnls if x < 0))
    summary["trades"] = len(pnls)
    summary["forced_exits"] = forced
    summary["wins"] = wins
    summary["losses"] = losses
    summary["winrate_pct"] = round(wins / len(pnls) * 100, 2) if pnls else 0.0
    summary["profit_factor"] = round(gross_win / gross_loss, 6) if gross_loss else (999.0 if gross_win else 0.0)
    summary["expectancy_usdt"] = round(sum(pnls) / len(pnls), 6) if pnls else 0.0
    summary["realized_pnl_usdt"] = round(sum(pnls), 6)
    summary["open_positions"] = len(bot.positions)
    summary["max_drawdown_usdt"] = round(peak - trough, 6)
    return summary


def _trade_rows(journal_dir: Path):
    path = Path(journal_dir) / "paper_trades.jsonl"
    if not path.exists():
        return []
    import json
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def load_feather_cycles(pairs: list[str], datadir: Path, limit: int | None = None):
    import pandas as pd  # optional: available in freqtrade container

    rows = []
    for pair in pairs:
        path = Path(datadir) / "bybit" / "futures" / f"{freqtrade_pair_to_file_stem(pair)}-5m-futures.feather"
        if not path.exists():
            continue
        df = pd.read_feather(path)
        if limit:
            df = df.tail(limit)
        if df.empty:
            continue
        close = df["close"].astype(float)
        high = df.get("high", close).astype(float)
        low = df.get("low", close).astype(float)
        delta = close.diff()
        gain = delta.clip(lower=0).rolling(14, min_periods=1).mean()
        loss = (-delta.clip(upper=0)).rolling(14, min_periods=1).mean()
        rsi = 100 - (100 / (1 + (gain / loss.replace(0, pd.NA))))
        prev = close.shift(1)
        tr = pd.concat([(high - low), (high - prev).abs(), (low - prev).abs()], axis=1).max(axis=1)
        df = df.copy()
        df["ema55"] = close.ewm(span=55, adjust=False).mean()
        df["rsi"] = rsi.fillna(50)
        df["atr_pct"] = (tr.rolling(14, min_periods=1).mean() / close * 100).fillna(0)
        for row in df.to_dict("records"):
            ts = row.get("date") or row.get("timestamp")
            if hasattr(ts, "timestamp"):
                now_ms = int(ts.timestamp() * 1000)
            else:
                now_ms = int(ts) if ts is not None else len(rows) * 300_000
            rows.append((now_ms, market_frame_from_row(pair, row)))
    buckets: dict[int, list[MarketFrame]] = {}
    for now_ms, frame in rows:
        buckets.setdefault(now_ms, []).append(frame)
    for now_ms in sorted(buckets):
        yield now_ms, buckets[now_ms]
