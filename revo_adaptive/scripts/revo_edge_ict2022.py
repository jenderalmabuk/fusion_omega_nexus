#!/usr/bin/env python3
"""Faithful ICT 2022 entry model (per Uncensored S&D + ICT Concepts books).

Bullish checklist (short = mirror):
  1. HTF bias bullish (close > EMA100)
  2. Liquidity sweep: recent bars take out the prior swing low (grab sellside)
  3. MSS + displacement: a candle closes above recent swing high with body >= 1*ATR
  4. FVG formed by the displacement (bullish: low[i] > high[i-2])
  5. Entry on retrace back INTO the FVG, in DISCOUNT (entry <= 50% of dealing range)
Edge measured first-touch +/-T% (decoupled from exit) + asymmetric 1:3 RR. ~50% = no edge.
"""
import glob, os
import numpy as np
import pandas as pd

DATA = os.environ.get("REVO_ICT_DATA", os.path.join(os.path.dirname(__file__), "_edge_data"))   # 1H price
SW, RANGE_W = 10, 40
DISP_ATR = 1.0
TTL = 24            # bars to wait for retrace into FVG
HORIZON = 48
THRESH = [2.0, 3.0]
rng = np.random.default_rng(7)


def atr(df, n=14):
    h, l, c = df["high"], df["low"], df["close"]
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    return tr.rolling(n).mean()


def signals(df):
    o, h, l, c = (df[x].values for x in ("open", "high", "low", "close"))
    a = atr(df).values
    ema = df["close"].ewm(span=100, adjust=False).mean().values
    body = np.abs(c - o); n = len(df); out = []
    for i in range(RANGE_W, n - HORIZON - 1):
        if np.isnan(a[i]) or a[i] <= 0:
            continue
        rng_hi, rng_lo = h[i-RANGE_W:i].max(), l[i-RANGE_W:i].min()
        eq = (rng_hi + rng_lo) / 2.0
        prior_low = l[i-SW-3:i-3].min(); prior_high = h[i-SW-3:i-3].max()
        recent_swing_high = h[i-SW:i].max(); recent_swing_low = l[i-SW:i].min()
        # ---- BULLISH ----
        if c[i] > ema[i]:                                            # 1 HTF bias
            swept = l[i-3:i+1].min() < prior_low                     # 2 sellside sweep
            mss = c[i] > recent_swing_high                           # 3 MSS up
            disp = c[i] > o[i] and body[i] >= DISP_ATR * a[i]
            fvg = l[i] > h[i-2]                                      # 4 bullish FVG
            if swept and mss and disp and fvg:
                fvg_top, fvg_bot = l[i], h[i-2]                      # 5 retrace into FVG, discount
                for k in range(i+1, min(i+1+TTL, n-1)):
                    if l[k] <= fvg_top:
                        entry = min(fvg_top, c[k])
                        if entry <= eq:
                            out.append((k, False, recent_swing_low)); break
        # ---- BEARISH ----
        if c[i] < ema[i]:
            swept = h[i-3:i+1].max() > prior_high
            mss = c[i] < recent_swing_low
            disp = c[i] < o[i] and body[i] >= DISP_ATR * a[i]
            fvg = h[i] < l[i-2]
            if swept and mss and disp and fvg:
                fvg_bot, fvg_top = h[i], l[i-2]
                for k in range(i+1, min(i+1+TTL, n-1)):
                    if h[k] >= fvg_bot:
                        entry = max(fvg_bot, c[k])
                        if entry >= eq:
                            out.append((k, True, recent_swing_high)); break
    return out


def first_touch(H, L, entry, short, thr):
    up, dn = entry*(1+thr/100), entry*(1-thr/100)
    for hi, lo in zip(H, L):
        if short:
            if lo <= dn: return "fav"
            if hi >= up: return "adv"
        else:
            if hi >= up: return "fav"
            if lo <= dn: return "adv"
    return "none"


def rr_outcome(H, L, entry, short, sl_price, rr=3.0):
    risk = abs(entry - sl_price)
    if risk <= 0: return 0.0
    tp = entry + rr*risk if not short else entry - rr*risk
    for hi, lo in zip(H, L):
        if short:
            if hi >= sl_price: return -1.0
            if lo <= tp: return rr
        else:
            if lo <= sl_price: return -1.0
            if hi >= tp: return rr
    return 0.0


res = {t: [0, 0, 0] for t in THRESH}; rnd = {t: [0, 0, 0] for t in THRESH}
rr_list = []; total = 0
for p in glob.glob(f"{DATA}/*.csv"):
    df = pd.read_csv(p)
    H, L, C = df["high"].values, df["low"].values, df["close"].values
    n = len(df); sig = signals(df); total += len(sig)
    for k, short, sl in sig:
        entry = C[k]; hs, ls = H[k+1:k+1+HORIZON], L[k+1:k+1+HORIZON]
        for t in THRESH:
            res[t][{"fav":0,"adv":1,"none":2}[first_touch(hs, ls, entry, short, t)]] += 1
        rr_list.append(rr_outcome(hs, ls, entry, short, sl, 3.0))
    for _ in range(len(sig)):
        k = int(rng.integers(RANGE_W, n-HORIZON-1)); short = bool(rng.integers(0, 2))
        entry = C[k]; hs, ls = H[k+1:k+1+HORIZON], L[k+1:k+1+HORIZON]
        for t in THRESH:
            rnd[t][{"fav":0,"adv":1,"none":2}[first_touch(hs, ls, entry, short, t)]] += 1

print(f"FAITHFUL ICT 2022 model | {total} signals | 1H 12 pairs | horizon {HORIZON}h\n")
print(f"{'thr':>5s} {'n':>5s} {'fav':>5s} {'adv':>5s} {'EDGE%':>6s}   {'random%':>8s}")
print("-"*48)
for t in THRESH:
    fav, adv, none = res[t]; dec = fav+adv
    edge = fav/dec*100 if dec else 0
    rf, ra, rn = rnd[t]; rdec = rf+ra
    print(f"{t:5.1f} {dec+none:5d} {fav:5d} {adv:5d} {edge:6.1f}   {(rf/rdec*100 if rdec else 0):8.1f}")
if rr_list:
    rr = np.array(rr_list); wins = (rr > 0).sum()
    pf = rr[rr > 0].sum()/abs(rr[rr < 0].sum()) if (rr < 0).any() else 99
    print(f"\nAsymmetric 1:3 RR (SL beyond sweep): n={len(rr)} WR={wins/len(rr)*100:.1f}% "
          f"expR={rr.mean():.3f} PF={pf:.2f}")
