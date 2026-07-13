#!/usr/bin/env python3
"""Extract key metrics from a freqtrade backtest result (.zip or .json).

Usage: revo_extract.py <result_path> <min_score> <discount> <rsi_max>
Emits one JSON line to stdout. <result_path> may be a .zip (modern freqtrade)
or a plain .json export.
"""
import json
import sys
import zipfile


def load_stats(path: str) -> dict:
    if path.endswith(".zip"):
        with zipfile.ZipFile(path) as z:
            names = [n for n in z.namelist()
                     if n.endswith(".json") and not n.endswith(".meta.json")]
            if not names:
                raise ValueError("no result json in zip")
            with z.open(names[0]) as f:
                return json.load(f)
    with open(path) as f:
        return json.load(f)


def main():
    path, ms, disc, rsi = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
    try:
        data = load_stats(path)
    except Exception as e:
        print(json.dumps({"min_score": ms, "discount": disc, "rsi_max": rsi,
                          "error": f"load_failed: {e}"}))
        return

    strat = data.get("strategy", {})
    if not strat:
        print(json.dumps({"min_score": ms, "discount": disc, "rsi_max": rsi,
                          "error": "no_strategy_key"}))
        return
    s = next(iter(strat.values()))

    trades = s.get("total_trades", 0)
    wins = s.get("wins", 0)
    losses = s.get("losses", 0)
    draws = s.get("draws", 0)
    winrate = round((wins / trades * 100), 1) if trades else 0.0
    out = {
        "min_score": ms,
        "discount": disc,
        "rsi_max": rsi,
        "trades": trades,
        "wins": wins,
        "losses": losses,
        "draws": draws,
        "winrate_pct": winrate,
        "profit_total_abs": round(s.get("profit_total_abs", 0), 2),
        "profit_total_pct": round(s.get("profit_total", 0) * 100, 2),
        "profit_mean_pct": round(s.get("profit_mean", 0) * 100, 3),
        "profit_factor": round(s.get("profit_factor", 0) or 0, 3),
        "expectancy": round(s.get("expectancy", 0) or 0, 4),
        "expectancy_ratio": round(s.get("expectancy_ratio", 0) or 0, 3),
        "max_drawdown_pct": round((s.get("max_drawdown_account", 0) or 0) * 100, 2),
        "max_drawdown_abs": round(s.get("max_drawdown_abs", 0) or 0, 2),
        "cagr": round(s.get("cagr", 0) or 0, 4),
        "sharpe": round(s.get("sharpe", 0) or 0, 3),
        "sortino": round(s.get("sortino", 0) or 0, 3),
        "avg_duration_min": s.get("holding_avg", ""),
    }
    print(json.dumps(out))


if __name__ == "__main__":
    main()
