"""Fidelity probe: does the backtest ignore SL hits on the FILL bar?

Live evidence (4 of 13 FusionNew trades) shows fill and SL landing in the same
5m candle. `_manage_exit` starts scanning at fill+1, so those losses are
invisible to the backtest. This measures how many trades change verdict when
the fill bar is included.
"""
import sys
sys.path.insert(0, "/app")
sys.path.insert(0, "/app/fusionnew")

import numpy as np
from backtest.faithful_imbalance import (
    TIERS, FIB_EXPIRY, _trend, generate_setups, _filter_flow, _filter_ema_dist,
    _filter_liquidity, _filter_stochastic,
)
from backtest.data import fetch_klines

DAYS = int(sys.argv[1]) if len(sys.argv) > 1 else 90
TIER = sys.argv[2] if len(sys.argv) > 2 else "H1"
FLOOR = float(sys.argv[3]) if len(sys.argv) > 3 else 0.0

SYMS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "LINKUSDT", "AVAXUSDT", "DOGEUSDT",
        "ADAUSDT", "BNBUSDT", "LTCUSDT", "NEARUSDT", "APTUSDT", "ARBUSDT", "OPUSDT",
        "INJUSDT", "TAOUSDT", "FILUSDT", "ATOMUSDT", "DOTUSDT", "UNIUSDT"]

same_bar = 0
total = 0
sl_pcts = []

for sym in SYMS:
    cfg = TIERS[TIER]
    try:
        zone_df = fetch_klines(sym, cfg["zone"], DAYS)
        ltf = fetch_klines(sym, cfg["ltf"], DAYS)
    except Exception:
        continue
    if min(len(zone_df), len(ltf)) < 260:
        continue
    trend = _trend(zone_df)
    btc = _trend(fetch_klines("BTCUSDT", cfg["zone"], DAYS))
    ll, lh = ltf["low"].to_numpy(), ltf["high"].to_numpy()
    for side in ("BULL", "BEAR"):
        s = generate_setups(zone_df, ltf, trend, side, sl_floor_pct=FLOOR)
        s = _filter_flow(sym, DAYS, side, s, ltf, True, False)
        s = _filter_ema_dist(s, zone_df, 1.0)
        s = _filter_liquidity(s, ltf, 1_000_000)
        s = _filter_stochastic(s, ltf, side, 70.0)
        for st in s:
            ce, entry, sl = st["ce"], st["entry"], st["sl"]
            fill = None
            for f in range(ce + 1, min(ce + 1 + FIB_EXPIRY, len(ltf))):
                if (side == "BULL" and ll[f] <= entry) or (side == "BEAR" and lh[f] >= entry):
                    fill = f
                    break
            if fill is None:
                continue
            total += 1
            sl_pcts.append(abs(entry - sl) / entry * 100)
            # would the SL have been hit on the fill bar itself?
            if (side == "BULL" and ll[fill] <= sl) or (side == "BEAR" and lh[fill] >= sl):
                same_bar += 1

print(f"tier={TIER} floor={FLOOR}% days={DAYS}")
print(f"filled trades          : {total}")
print(f"SL hit on FILL bar     : {same_bar}  ({same_bar / total * 100 if total else 0:.1f}%)")
print(f"  -> these are counted as survivors by _manage_exit (loop starts fill+1)")
if sl_pcts:
    print(f"median SL distance     : {np.median(sl_pcts):.3f}%")
