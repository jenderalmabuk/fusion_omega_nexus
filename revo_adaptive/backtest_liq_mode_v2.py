#!/usr/bin/env python3
"""Backtest RevoAdaptiveStrategy: compare liq_mode=instant vs liq_mode=med48.

Runs freqtrade backtesting on 93 pairs, 5m timeframe, Jun 1 - Jul 10 (40 days).
Uses docker container for isolation and consistent data.
"""
import os
import json
import subprocess
import tempfile
from datetime import datetime, timedelta

REPO_ROOT = "/home/fusion_omega/fusion_omega_nexus"
REVO_DIR = os.path.join(REPO_ROOT, "revo_adaptive")

# base env for both modes
BASE_ENV = {
    "BYBIT_API_KEY": "dummy",
    "BYBIT_API_SECRET": "dummy",
    "REVO_ENTRY_MIN_SCORE": "9",
    "REVO_ENTRY_DISCOUNT_MIN_PCT": "3.5",
    "REVO_ENTRY_DISCOUNT_MAX_PCT": "9",
    "REVO_ENTRY_RSI_MAX": "40",
    "REVO_MIN_QVOL_5M": "200000",
    "REVO_ER_CHOP_MAX": "0.15",
    "REVO_ATR_PCT_MAX": "4.0",
    "REVO_FLOW_MAX_AGE_SEC": "660",
    "REVO_FLOW_CONTEXT_PATH": "/freqtrade/user_data/local/revo_alpha/runtime/bybit/revo_flow_context.json",
    "REVO_ENTRY_AUDIT_PATH": "/freqtrade/user_data/local/revo_entry_audit.jsonl",
}

MODES = [
    ("instant", "instant"),
    ("med48", "med48"),
]

def run_backtest(mode: str) -> dict:
    """Run freqtrade backtest and return summary metrics."""
    env = {**BASE_ENV, "REVO_LIQ_MODE": mode}
    env_str = " ".join(f"-e {k}='{v}'" for k, v in env.items())

    # Use docker to run backtest in the revo container context
    cmd = f"""docker run --rm \\
        {env_str} \\
        -v {REVO_DIR}/user_data:/freqtrade/user_data \\
        -v {REPO_ROOT}/runtime/revo:/external_runtime \\
        ghcr.io/freqtrade/freqtrade:develop \\
        backtesting \\
        --config /freqtrade/user_data/configs/config.bybit.backtest.json \\
        --strategy RevoAdaptiveStrategy \\
        --timerange 20260601-20260710 \\
        --export trades \\
        --breakdown day week month \\
        2>&1"""

    print(f"\n{'='*60}")
    print(f"Running backtest: liq_mode={mode}")
    print(f"{'='*60}")
    print(f"Command: {cmd[:200]}...")
    
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=1800)
    
    # Parse output for key metrics
    output = result.stdout + result.stderr
    
    # Save full output
    out_file = f"/tmp/revo_backtest_{mode}_{datetime.now().strftime('%H%M%S')}.log"
    with open(out_file, "w") as f:
        f.write(output)
    print(f"Full log: {out_file}")
    
    # Extract summary
    metrics = {"mode": mode, "return_code": result.returncode}
    
    for line in output.splitlines():
        if "Total profit" in line or "Total Profit" in line:
            metrics["total_profit"] = line.strip()
        if "Total trades" in line or "Total Trades" in line:
            metrics["total_trades"] = line.strip()
        if "Win rate" in line or "Win Rate" in line:
            metrics["win_rate"] = line.strip()
        if "Profit factor" in line or "Profit Factor" in line:
            metrics["profit_factor"] = line.strip()
        if "Max drawdown" in line or "Max Drawdown" in line:
            metrics["max_drawdown"] = line.strip()
        if "Avg. profit" in line or "Avg. Profit" in line:
            metrics["avg_profit"] = line.strip()
        if "Avg. duration" in line or "Avg. Duration" in line:
            metrics["avg_duration"] = line.strip()
    
    print(f"  Return code: {result.returncode}")
    for k, v in metrics.items():
        if k not in ("mode", "return_code"):
            print(f"  {k}: {v}")
    
    return metrics

def main():
    print("Revo Adaptive Backtest: liq_mode comparison")
    print("=" * 60)
    
    results = []
    for mode_name, mode_val in MODES:
        try:
            metrics = run_backtest(mode_val)
            results.append(metrics)
        except subprocess.TimeoutExpired:
            print(f"  TIMEOUT for {mode_name}")
            results.append({"mode": mode_name, "error": "timeout"})
        except Exception as e:
            print(f"  ERROR for {mode_name}: {e}")
            results.append({"mode": mode_name, "error": str(e)})
    
    # Summary
    print("\n" + "=" * 60)
    print("SUMMARY COMPARISON")
    print("=" * 60)
    for r in results:
        if "error" in r:
            print(f"{r['mode']:10s}: ERROR - {r['error']}")
        else:
            print(f"{r['mode']:10s}: trades={r.get('total_trades','?')} "
                  f"profit={r.get('total_profit','?')} "
                  f"wr={r.get('win_rate','?')} "
                  f"pf={r.get('profit_factor','?')} "
                  f"dd={r.get('max_drawdown','?')}")
    
    # Save JSON
    summary_file = f"/tmp/revo_backtest_summary_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(summary_file, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nSummary saved: {summary_file}")

if __name__ == "__main__":
    main()