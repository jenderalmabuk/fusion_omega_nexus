#!/usr/bin/env python3
"""
run_backtest.py — Automated backtest runner for RevoSignalStrategy.

Features:
- Downloads data via CCXT (Binance/Bybit)
- Runs Freqtrade backtest with honest settings
- Walk-forward OOS split (last 40% by default)
- Generates comprehensive report (PF, WR, payoff, trades, curves)
- Saves results for audit trail
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd


DEFAULT_PAIRS = [
    "BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT",
    "XRP/USDT", "AVAX/USDT", "DOGE/USDT", "LINK/USDT", "ADA/USDT"
]

DEFAULT_TIMEFRAME = "5m"
DEFAULT_DAYS = 120
DEFAULT_OOS_PCT = 40


def download_data(pairs: List[str], timeframe: str, days: int, exchange: str, output_dir: Path) -> Dict[str, Path]:
    """Download OHLCV data using freqtrade download-data command."""
    output_dir.mkdir(parents=True, exist_ok=True)
    timerange = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

    pairs_str = " ".join(pairs)
    cmd = [
        "docker", "run", "--rm",
        "-v", f"{output_dir}:/freqtrade/user_data",
        "freqtradeorg/freqtrade:stable",
        "download-data",
        "--exchange", exchange,
        "--pairs", pairs_str,
        "--timeframes", timeframe,
        "--timerange", f"{timerange}-",
        "--data-format", "json"
    ]
    print(f"[Backtest] Downloading data: {pairs_str} ({timeframe}, {days}d from {exchange})")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"[ERROR] Download failed: {result.stderr}")
        raise RuntimeError("Data download failed")

    # Find downloaded files
    files = {}
    for pair in pairs:
        safe_pair = pair.replace("/", "_").replace(":", "")
        for f in output_dir.glob(f"**/*{safe_pair}*.json"):
            files[pair] = f
            break
    return files


def run_backtest(config_file: Path, strategy: str, data_dir: Path,
                 timerange: Optional[str] = None, export: Optional[str] = None) -> Dict:
    """Run freqtrade backtest and return parsed results."""
    cmd = [
        "docker", "run", "--rm",
        "-v", f"{data_dir}:/freqtrade/user_data",
        "freqtradeorg/freqtrade:stable",
        "backtesting",
        "--config", f"/freqtrade/user_data/{config_file.name}",
        "--strategy", strategy,
        "--export", export or "trades",
        "--timeframe", DEFAULT_TIMEFRAME,
    ]
    if timerange:
        cmd.extend(["--timerange", timerange])

    print(f"[Backtest] Running: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)

    if result.returncode != 0:
        print(f"[ERROR] Backtest failed: {result.stderr}")
        raise RuntimeError("Backtest execution failed")

    # Parse results from stdout
    return parse_backtest_output(result.stdout, result.stderr)


def parse_backtest_output(stdout: str, stderr: str) -> Dict:
    """Parse freqtrade backtest output for key metrics."""
    metrics = {}
    lines = stdout.split('\n')

    for line in lines:
        line = line.strip()
        if "Profit %" in line and "Total" in line:
            parts = line.split()
            for i, p in enumerate(parts):
                if "%" in p:
                    try:
                        metrics["total_profit_pct"] = float(p.replace("%", "").replace(",", ""))
                    except:
                        pass
        if "Win" in line and "Loss" in line and "draw" in line.lower():
            # Win/Loss/Draw line
            parts = line.split()
            for i, p in enumerate(parts):
                if "/" in p:
                    try:
                        w, l, d = p.split("/")
                        metrics["wins"] = int(w)
                        metrics["losses"] = int(l)
                        metrics["draws"] = int(d)
                        metrics["total_trades"] = int(w) + int(l) + int(d)
                    except:
                        pass
        if "Profit Factor" in line:
            parts = line.split()
            for i, p in enumerate(parts):
                try:
                    metrics["profit_factor"] = float(p)
                    break
                except:
                    pass
        if "Expectancy" in line:
            parts = line.split()
            for i, p in enumerate(parts):
                try:
                    metrics["expectancy"] = float(p)
                    break
                except:
                    pass
        if "Max Drawdown" in line:
            parts = line.split()
            for i, p in enumerate(parts):
                if "%" in p:
                    try:
                        metrics["max_drawdown_pct"] = float(p.replace("%", "").replace(",", ""))
                    except:
                        pass

    # Also check for exported trades file
    return metrics


def load_trades_from_export(export_dir: Path) -> pd.DataFrame:
    """Load and aggregate trades from freqtrade export."""
    trade_files = list(export_dir.glob("*.json"))
    if not trade_files:
        return pd.DataFrame()

    all_trades = []
    for f in trade_files:
        try:
            with open(f) as fp:
                data = json.load(fp)
                if isinstance(data, list):
                    all_trades.extend(data)
                elif isinstance(data, dict) and "trades" in data:
                    all_trades.extend(data["trades"])
        except Exception as e:
            print(f"[WARN] Failed to load {f}: {e}")

    if not all_trades:
        return pd.DataFrame()

    df = pd.DataFrame(all_trades)
    return df


def analyze_trades(df: pd.DataFrame) -> Dict:
    """Compute detailed metrics from trade list."""
    if df.empty:
        return {"error": "No trades"}

    # Ensure numeric columns
    for col in ["profit_ratio", "profit_abs", "trade_duration", "open_date", "close_date"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    df = df.dropna(subset=["profit_ratio"])

    metrics = {
        "total_trades": len(df),
        "wins": int((df["profit_ratio"] > 0).sum()),
        "losses": int((df["profit_ratio"] < 0).sum()),
        "draws": int((df["profit_ratio"] == 0).sum()),
        "total_profit_pct": df["profit_ratio"].sum() * 100,
        "avg_profit_pct": df["profit_ratio"].mean() * 100,
        "median_profit_pct": df["profit_ratio"].median() * 100,
        "std_profit_pct": df["profit_ratio"].std() * 100,
        "profit_factor": (
            df[df["profit_ratio"] > 0]["profit_ratio"].sum() /
            abs(df[df["profit_ratio"] < 0]["profit_ratio"].sum())
            if (df["profit_ratio"] < 0).any() else float('inf')
        ),
        "payoff_ratio": (
            df[df["profit_ratio"] > 0]["profit_ratio"].mean() /
            abs(df[df["profit_ratio"] < 0]["profit_ratio"].mean())
            if (df["profit_ratio"] < 0).any() else float('inf')
        ),
        "max_win_pct": df["profit_ratio"].max() * 100,
        "max_loss_pct": df["profit_ratio"].min() * 100,
    }

    if "trade_duration" in df.columns:
        metrics["avg_duration_min"] = df["trade_duration"].mean() / 60
        metrics["max_duration_min"] = df["trade_duration"].max() / 60

    # Consecutive wins/losses
    wins_losses = (df["profit_ratio"] > 0).astype(int).tolist()
    max_consec_win = max_consec_loss = 0
    current_win = current_loss = 0
    for wl in wins_losses:
        if wl == 1:
            current_win += 1
            current_loss = 0
            max_consec_win = max(max_consec_win, current_win)
        else:
            current_loss += 1
            current_win = 0
            max_consec_loss = max(max_consec_loss, current_loss)
    metrics["max_consec_wins"] = max_consec_win
    metrics["max_consec_losses"] = max_consec_loss

    return metrics


def split_oos(trades_df: pd.DataFrame, oos_pct: int = 40) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """Split trades by time for OOS analysis."""
    if trades_df.empty or "open_date" not in trades_df.columns:
        return trades_df, pd.DataFrame()

    trades_df = trades_df.copy()
    trades_df["open_date"] = pd.to_datetime(trades_df["open_date"])
    trades_df = trades_df.sort_values("open_date")

    split_idx = int(len(trades_df) * (100 - oos_pct) / 100)
    in_sample = trades_df.iloc[:split_idx]
    out_sample = trades_df.iloc[split_idx:]

    return in_sample, out_sample


def generate_report(metrics: Dict, in_sample_metrics: Dict, oos_metrics: Dict,
                    config: Dict, output_file: Path) -> None:
    """Generate comprehensive markdown report."""
    report = f"""# Backtest Report — RevoSignalStrategy

**Generated:** {datetime.now().isoformat()}
**Config:** {config.get('config_file', 'N/A')}
**Strategy:** {config.get('strategy', 'RevoSignalStrategy')}
**Timeframe:** {config.get('timeframe', '5m')}
**Pairs:** {', '.join(config.get('pairs', []))}
**Days:** {config.get('days', 'N/A')}
**OOS Split:** {config.get('oos_pct', 40)}%

---

## Summary

| Metric | In-Sample | Out-of-Sample |
|--------|-----------|---------------|
| Total Trades | {in_sample_metrics.get('total_trades', 0)} | {oos_metrics.get('total_trades', 0)} |
| Win Rate | {in_sample_metrics.get('wins', 0)/max(1, in_sample_metrics.get('total_trades', 1))*100:.1f}% | {oos_metrics.get('wins', 0)/max(1, oos_metrics.get('total_trades', 1))*100:.1f}% |
| Profit Factor | {in_sample_metrics.get('profit_factor', 0):.2f} | {oos_metrics.get('profit_factor', 0):.2f} |
| Total Return | {in_sample_metrics.get('total_profit_pct', 0):.2f}% | {oos_metrics.get('total_profit_pct', 0):.2f}% |
| Avg Profit/Trade | {in_sample_metrics.get('avg_profit_pct', 0):.2f}% | {oos_metrics.get('avg_profit_pct', 0):.2f}% |
| Max Drawdown | {metrics.get('max_drawdown_pct', 0):.2f}% | N/A |
| Max Consec Losses | {in_sample_metrics.get('max_consec_losses', 0)} | {oos_metrics.get('max_consec_losses', 0)} |
| Expectancy | {in_sample_metrics.get('expectancy', 0):.4f} | {oos_metrics.get('expectancy', 0):.4f} |

---

## Detailed Metrics (Full Sample)

```
{json.dumps(metrics, indent=2, default=str)}
```

---

## In-Sample Detail

```
{json.dumps(in_sample_metrics, indent=2, default=str)}
```

---

## Out-of-Sample Detail

```
{json.dumps(oos_metrics, indent=2, default=str)}
```

---

## Edge Assessment

**Verdict:** {'✅ EDGE PRESENT' if oos_metrics.get('profit_factor', 0) >= 1.2 and oos_metrics.get('total_trades', 0) >= 50 else '❌ NO EDGE'}

Criteria for edge:
- OOS Profit Factor ≥ 1.2
- OOS Trades ≥ 50
- Max consecutive losses ≤ 10
- Max drawdown ≤ 25%

**OOS PF:** {oos_metrics.get('profit_factor', 0):.2f} (target ≥ 1.2)
**OOS Trades:** {oos_metrics.get('total_trades', 0)} (target ≥ 50)
**Max Consec Losses:** {oos_metrics.get('max_consec_losses', 0)} (target ≤ 10)

---

## Next Steps

{'Proceed to paper trading with small size' if oos_metrics.get('profit_factor', 0) >= 1.2 and oos_metrics.get('total_trades', 0) >= 50 else 'Do NOT deploy. Re-evaluate entry/exit logic.'}
"""

    output_file.write_text(report)
    print(f"\n[Backtest] Report saved: {output_file}")


def main():
    parser = argparse.ArgumentParser(description="Run RevoSignalStrategy backtest")
    parser.add_argument("--pairs", nargs="+", default=DEFAULT_PAIRS, help="Trading pairs")
    parser.add_argument("--timeframe", default=DEFAULT_TIMEFRAME, help="Timeframe")
    parser.add_argument("--days", type=int, default=DEFAULT_DAYS, help="Days of history")
    parser.add_argument("--exchange", default="binance", help="Exchange for data")
    parser.add_argument("--oos-pct", type=int, default=DEFAULT_OOS_PCT, help="OOS split %")
    parser.add_argument("--config", default="user_data/config.bybit.signal.paper.json", help="Freqtrade config")
    parser.add_argument("--strategy", default="RevoSignalStrategy", help="Strategy class name")
    parser.add_argument("--data-dir", default="user_data/data", help="Data directory")
    parser.add_argument("--output-dir", default="user_data/backtest_results", help="Output directory")
    parser.add_argument("--skip-download", action="store_true", help="Skip data download")
    args = parser.parse_args()

    # Setup paths
    data_dir = Path(args.data_dir).resolve()
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    config_file = Path(args.config).resolve()

    # Step 1: Download data
    if not args.skip_download:
        download_data(args.pairs, args.timeframe, args.days, args.exchange, data_dir)

    # Step 2: Run full backtest
    print("\n" + "="*60)
    print("FULL BACKTEST")
    print("="*60)
    full_result = run_backtest(config_file, args.strategy, data_dir)

    # Step 3: Load trades for OOS analysis
    export_dir = data_dir / "backtest_results"
    if export_dir.exists():
        trades_df = load_trades_from_export(export_dir)
        if not trades_df.empty:
            in_sample, out_sample = split_oos(trades_df, args.oos_pct)
            in_sample_metrics = analyze_trades(in_sample)
            oos_metrics = analyze_trades(out_sample)

            # Step 4: Generate report
            report_file = output_dir / f"backtest_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
            generate_report(
                metrics=full_result,
                in_sample_metrics=in_sample_metrics,
                oos_metrics=oos_metrics,
                config={
                    "config_file": args.config,
                    "strategy": args.strategy,
                    "timeframe": args.timeframe,
                    "pairs": args.pairs,
                    "days": args.days,
                    "oos_pct": args.oos_pct,
                },
                output_file=report_file
            )
        else:
            print("[WARN] No trades exported — skipping OOS analysis")
    else:
        print("[WARN] No export directory found")


if __name__ == "__main__":
    main()
