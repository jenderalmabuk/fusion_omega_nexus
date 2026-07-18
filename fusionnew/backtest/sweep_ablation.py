"""Ablation sweep: isolasi kontribusi tiap filter (btc-regime / cvd / min-turn) lintas tier.
Menjawab: (1) btc-regime hentikan short-saat-BTC-up? (2) cvd bantu? (3) likuiditas bantu?
(4) TF kecil (M15) dgn variasi likuiditas. Impor fungsi harness langsung (bukan parse stdout).
"""
from __future__ import annotations
import sys, json, time
import numpy as np
sys.path.insert(0, "/home/fusion_omega/fusion_omega_nexus/fusionnew")
from backtest.faithful_imbalance import _simulate_symbol

EQUITY0 = 1000.0
PAIRS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "AVAXUSDT", "INJUSDT",
         "DOGEUSDT", "WLDUSDT", "XLMUSDT", "LINKUSDT", "NEARUSDT", "DOTUSDT"]
DAYS = 120
OOS = 0.40


def metrics(trades):
    if not trades:
        return dict(n=0, wr=0.0, pf=0.0, net=0.0, maxdd=0.0, exp=0.0)
    equity = EQUITY0
    pnls, eq = [], []
    for t in trades:
        qty = (equity * t["risk_pct"]) / max(t["risk"], 1e-9)
        pnl = qty * t["per_unit"]
        equity += pnl
        pnls.append(pnl); eq.append(equity)
    pnls = np.array(pnls)
    wins = pnls[pnls > 0]; losses = pnls[pnls <= 0]
    wr = len(wins) / len(pnls) * 100
    pf = wins.sum() / abs(losses.sum()) if losses.sum() != 0 else float("inf")
    peak, maxdd = -1e18, 0.0
    for e in eq:
        peak = max(peak, e); maxdd = max(maxdd, peak - e)
    return dict(n=len(pnls), wr=round(wr, 1), pf=round(pf, 2),
                net=round(pnls.sum(), 1), maxdd=round(maxdd, 1),
                exp=round(pnls.mean(), 2))


def run_variant(tier, direction, use_cvd, use_btc, min_turn, ema_dist, stoch_max=0.0):
    all_t = []
    for sym in PAIRS:
        try:
            tr = _simulate_symbol(sym, tier, DAYS, direction, 2.0,
                                  use_cvd, False, use_btc, False,
                                  "fixed", ema_dist, min_turn, stoch_max)
            all_t.extend(tr)
        except Exception as e:
            print(f"    {sym} ERR: {e}", flush=True)
    all_t.sort(key=lambda t: t["t_entry"])
    split = int(len(all_t) * (1 - OOS))
    return metrics(all_t), metrics(all_t[split:])


# (label, tier, direction, cvd, btc, min_turn, ema_dist, stoch_max)
# stoch_max=50 = setting LIVE saat ini; 0 = OFF. A/B utk isolasi dampak stochastic.
MATRIX = [
    # === STOCH SENDIRIAN (tanpa btc, tanpa cvd) — isolasi murni utk user ===
    # H1
    ("H1 stoch OFF",            "H1", "both", False, False, 0, 0.0, 0),
    ("H1 stoch50 only",         "H1", "both", False, False, 0, 0.0, 50),
    ("H1 stoch60 only",         "H1", "both", False, False, 0, 0.0, 60),
    ("H1 stoch70 only",         "H1", "both", False, False, 0, 0.0, 70),
    ("H1 stoch80 only",         "H1", "both", False, False, 0, 0.0, 80),
    # M30
    ("M30 stoch OFF",           "M30", "both", False, False, 0, 0.0, 0),
    ("M30 stoch50 only",        "M30", "both", False, False, 0, 0.0, 50),
    ("M30 stoch60 only",        "M30", "both", False, False, 0, 0.0, 60),
    ("M30 stoch70 only",        "M30", "both", False, False, 0, 0.0, 70),
    ("M30 stoch80 only",        "M30", "both", False, False, 0, 0.0, 80),
    # === PEMBANDING: stoch70 + btc+cvd (referensi stack penuh) ===
    ("H1 stoch70+btc+cvd",      "H1", "both", True,  True,  0, 0.0, 70),
    ("M30 stoch70+btc+cvd",     "M30", "both", True,  True,  0, 0.0, 70),
]

if __name__ == "__main__":
    print(f"SWEEP ablation | {len(PAIRS)} pairs | {DAYS}d | OOS {int(OOS*100)}%", flush=True)
    print("=" * 100, flush=True)
    results = []
    t0 = time.time()
    for label, tier, direction, cvd, btc, mt, ema, stoch in MATRIX:
        ta = time.time()
        full, oos = run_variant(tier, direction, cvd, btc, mt, ema, stoch)
        dt = time.time() - ta
        results.append(dict(label=label, tier=tier, dir=direction, full=full, oos=oos))
        print(f"  {label:22} | ALL n={full['n']:3} WR={full['wr']:5}% PF={full['pf']:5} net={full['net']:8} DD={full['maxdd']:6} "
              f"|| OOS n={oos['n']:3} WR={oos['wr']:5}% PF={oos['pf']:5} net={oos['net']:8} DD={oos['maxdd']:6}  ({dt:.0f}s)", flush=True)
    print("=" * 100, flush=True)
    print(f"DONE in {time.time()-t0:.0f}s", flush=True)
    with open("/home/fusion_omega/fusion_omega_nexus/fusionnew/backtest/sweep_ablation_result.json", "w") as f:
        json.dump(results, f, indent=2)
    print("saved: backtest/sweep_ablation_result.json", flush=True)
