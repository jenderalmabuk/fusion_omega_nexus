"""Ablation: SL floor % — chronological IS/OOS split, multi-pair.

Isolates ONE variable (sl_floor_pct). Everything else matches the live FusionNew
config. Split is chronological: first 60% of the window = IS, last 40% = OOS.
"""
import sys, json, statistics
sys.path.insert(0, "/app")
sys.path.insert(0, "/app/fusionnew")

import numpy as np
import pandas as pd
from backtest.faithful_imbalance import _simulate_symbol

DAYS = int(sys.argv[1]) if len(sys.argv) > 1 else 90
TIER = sys.argv[2] if len(sys.argv) > 2 else "H1"
DIRECTION = sys.argv[3] if len(sys.argv) > 3 else "both"
STOCH = float(sys.argv[4]) if len(sys.argv) > 4 else 70.0
FLOORS = [0.0, 1.0, 1.5, 2.0, 2.5]

SYMS = ["BTCUSDT","ETHUSDT","SOLUSDT","XRPUSDT","LINKUSDT","AVAXUSDT","DOGEUSDT",
        "ADAUSDT","BNBUSDT","LTCUSDT","NEARUSDT","APTUSDT","ARBUSDT","OPUSDT",
        "INJUSDT","TAOUSDT","FILUSDT","ATOMUSDT","DOTUSDT","UNIUSDT"]


def metrics(trades):
    if not trades:
        return {"n": 0, "wr": 0.0, "pf": 0.0, "net": 0.0, "avg_sl_pct": 0.0}
    net = 0.0
    gp = gl = 0.0
    wins = 0
    slp = []
    for t in trades:
        risk_amt = 10000 * t["risk_pct"]
        r_mult = t["per_unit"] / t["risk"] if t["risk"] else 0.0
        pnl = r_mult * risk_amt
        net += pnl
        if pnl > 0:
            wins += 1
            gp += pnl
        else:
            gl += abs(pnl)
        slp.append(abs(t["entry"] - t["sl"]) / t["entry"] * 100)
    return {"n": len(trades), "wr": wins / len(trades) * 100,
            "pf": (gp / gl) if gl else float("inf"),
            "net": net, "avg_sl_pct": statistics.median(slp)}


def split(trades):
    """Chronological IS/OOS split on entry time."""
    if not trades:
        return [], []
    ts = sorted(trades, key=lambda t: t["t_entry"])
    cut = int(len(ts) * 0.6)
    return ts[:cut], ts[cut:]


print(f"# SL-floor ablation  tier={TIER} dir={DIRECTION} days={DAYS} stoch={STOCH} syms={len(SYMS)}")
print(f"{'floor%':>7} | {'IS n':>5} {'IS WR':>6} {'IS PF':>6} {'IS net':>9} | "
      f"{'OOS n':>5} {'OOS WR':>6} {'OOS PF':>6} {'OOS net':>9} | {'medSL%':>6}")
print("-" * 96)

results = {}
for floor in FLOORS:
    allt = []
    for sym in SYMS:
        try:
            allt += _simulate_symbol(sym, TIER, DAYS, DIRECTION, use_cvd=True, use_btc=True,
                                     ema_dist=1.0, min_turn=1_000_000, stoch_max=STOCH,
                                     sl_floor_pct=floor)
        except Exception:
            continue
    is_t, oos_t = split(allt)
    mi, mo, ma = metrics(is_t), metrics(oos_t), metrics(allt)
    results[floor] = {"is": mi, "oos": mo, "all": ma}
    print(f"{floor:7.1f} | {mi['n']:5d} {mi['wr']:5.1f}% {mi['pf']:6.2f} {mi['net']:+9.2f} | "
          f"{mo['n']:5d} {mo['wr']:5.1f}% {mo['pf']:6.2f} {mo['net']:+9.2f} | {ma['avg_sl_pct']:6.2f}")

print()
ok = [f for f, r in results.items()
      if r["is"]["pf"] > 1.0 and r["oos"]["pf"] > 1.0 and r["oos"]["n"] >= 20]
print("PASS (IS PF>1 AND OOS PF>1 AND OOS n>=20):", ok if ok else "NONE")
json.dump({str(k): v for k, v in results.items()},
          open(f"/tmp/sl_floor_ablation_{TIER}_{DIRECTION}.json", "w"), indent=2, default=str)
