"""Why does live diverge from backtest? Isolate the two prime suspects.

The reported backtest numbers used `generate_setups` on 20 majors. The LIVE engine
uses `nearest_unmitigated_setups` on a 465-symbol universe. Those are two different
strategies on two different universes — so the backtest was never measuring live.

Variants (everything else held constant):
  A  majors20 + generate_setups        <- what the backtest reported
  B  majors20 + nearest_unmitigated    <- swap detector only
  C  live60   + nearest_unmitigated    <- swap detector AND universe (= live)
"""
import sys, statistics
sys.path.insert(0, "/app")
sys.path.insert(0, "/app/fusionnew")

import numpy as np
from backtest.faithful_imbalance import (
    TIERS, FIB_EXPIRY, RR, _trend, generate_setups, nearest_unmitigated_setups,
    _filter_flow, _filter_ema_dist, _filter_liquidity, _filter_stochastic,
    _manage_exit, _atr, _trend_ok_strong,
)
from backtest.data import fetch_klines

DAYS = int(sys.argv[1]) if len(sys.argv) > 1 else 90
TIER = sys.argv[2] if len(sys.argv) > 2 else "H1"
FLOOR = float(sys.argv[3]) if len(sys.argv) > 3 else 1.0
STOCH = 70.0

MAJORS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "LINKUSDT", "AVAXUSDT", "DOGEUSDT",
          "ADAUSDT", "BNBUSDT", "LTCUSDT", "NEARUSDT", "APTUSDT", "ARBUSDT", "OPUSDT",
          "INJUSDT", "TAOUSDT", "FILUSDT", "ATOMUSDT", "DOTUSDT", "UNIUSDT"]

univ = [s.strip() for s in open("/app/runtime/revo/canonical_universe.txt") if s.strip()]
# deterministic spread across the live universe (every Nth symbol), 60 names
step = max(1, len(univ) // 60)
LIVE60 = univ[::step][:60]


def metrics(tr):
    if not tr:
        return dict(n=0, wr=0.0, pf=0.0, net=0.0, sl=0.0)
    net = gp = gl = 0.0
    w = 0
    slp = []
    for t in tr:
        r = t["per_unit"] / t["risk"] if t["risk"] else 0.0
        pnl = r * 10000 * 0.01
        net += pnl
        if pnl > 0:
            w += 1
            gp += pnl
        else:
            gl += abs(pnl)
        slp.append(abs(t["entry"] - t["sl"]) / t["entry"] * 100)
    return dict(n=len(tr), wr=w / len(tr) * 100, pf=(gp / gl if gl else float("inf")),
                net=net, sl=statistics.median(slp))


def sim(syms, detector, max_age):
    cfg = TIERS[TIER]
    out = []
    btc_cache = {}
    for sym in syms:
        try:
            z = fetch_klines(sym, cfg["zone"], DAYS)
            l = fetch_klines(sym, cfg["ltf"], DAYS)
        except Exception:
            continue
        if min(len(z), len(l)) < 260:
            continue
        t = _trend(z)
        if "btc" not in btc_cache:
            btc_cache["btc"] = _trend(fetch_klines("BTCUSDT", cfg["zone"], DAYS))
        btc = btc_cache["btc"]
        ll, lh, lc = l["low"].to_numpy(), l["high"].to_numpy(), l["close"].to_numpy()
        latr = _atr(l)
        for side in ("BULL", "BEAR"):
            if detector is generate_setups:
                s = detector(z, l, t, side, RR, sl_floor_pct=FLOOR)
            else:
                s = detector(z, l, t, side, RR, max_age=max_age, sl_floor_pct=FLOOR)
            s = _filter_flow(sym, DAYS, side, s, l, True, False)
            s = [x for x in s if _trend_ok_strong(btc, x["t_complete"], side, 0.75)]
            s = _filter_ema_dist(s, z, 1.0)
            s = _filter_liquidity(s, l, 1_000_000)
            s = _filter_stochastic(s, l, side, STOCH)
            for st in s:
                ce, entry, sl = st["ce"], st["entry"], st["sl"]
                fill = None
                for f in range(ce + 1, min(ce + 1 + FIB_EXPIRY, len(l))):
                    if (side == "BULL" and ll[f] <= entry) or (side == "BEAR" and lh[f] >= entry):
                        fill = f
                        break
                if fill is None:
                    continue
                pu, reason = _manage_exit(side, entry, sl, st["tp"], ll, lh, lc, latr, fill, "fixed")
                out.append(dict(symbol=sym, side=side, entry=entry, sl=sl,
                                per_unit=pu, risk=st["risk"], reason=reason))
    return out


print(f"# backtest-vs-live divergence  tier={TIER} days={DAYS} floor={FLOOR}% stoch={STOCH}")
print(f"{'variant':<34} | {'n':>4} {'WR':>6} {'PF':>6} {'net':>10} {'medSL%':>7}")
print("-" * 78)
for label, syms, det, age in [
    ("A majors20 + generate_setups", MAJORS, generate_setups, 0),
    ("B majors20 + nearest_unmitig", MAJORS, nearest_unmitigated_setups, 288),
    ("C live60   + nearest_unmitig", LIVE60, nearest_unmitigated_setups, 288),
]:
    tr = sim(syms, det, age)
    m = metrics(tr)
    print(f"{label:<34} | {m['n']:4d} {m['wr']:5.1f}% {m['pf']:6.2f} {m['net']:+10.2f} {m['sl']:7.3f}")
    rs = {}
    for t in tr:
        rs[t["reason"]] = rs.get(t["reason"], 0) + 1
    print(f"{'':34} |   exits: {rs}")
