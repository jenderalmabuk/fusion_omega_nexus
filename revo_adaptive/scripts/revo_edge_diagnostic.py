#!/usr/bin/env python3
"""Raw entry-edge diagnostic — decoupled from exit logic.

For each historical entry, walk the forward price series and measure:
- MFE/MAE at fixed time horizons (no SL/TP applied)
- First-touch edge: does price hit +T% (favorable) before -T% (adverse)?
A signal with NO directional edge scores ~50% on symmetric first-touch.
"""
import sqlite3
from datetime import datetime, timedelta
from collections import defaultdict

RUNTIME = "/home/fusion_omega/revo_adaptive/user_data/revo_alpha/runtime/bybit"
DB = "/home/fusion_omega/revo_adaptive/user_data/tradesv3_revo_v13914f2_bybit_dynamic_watch_promote.dryrun.sqlite"
PRICE_DB = f"{RUNTIME}/f3a_market_wide_flow_cache.sqlite"
HORIZONS_MIN = [60, 120, 240]
TOUCH_THRESHOLDS = [1.0, 1.5, 2.0]


def pdt(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


# Forward price series per pair from scanner snapshots
pc = sqlite3.connect(PRICE_DB)
series = defaultdict(list)
for ts, pair, px in pc.execute("SELECT ts, pair, last_price FROM eligible_pairs WHERE last_price > 0 ORDER BY ts"):
    series[pair].append((pdt(ts), float(px)))
pc.close()

tc = sqlite3.connect(DB)
tc.row_factory = sqlite3.Row
trades = tc.execute(
    "SELECT pair, is_short, open_date, open_rate, close_profit FROM trades WHERE close_date IS NOT NULL"
).fetchall()
tc.close()


def fwd(pair, start, horizon_min):
    end = start + timedelta(minutes=horizon_min)
    return [px for t, px in series.get(pair, []) if start <= t <= end]


mfe = {h: {"win": [], "loss": []} for h in HORIZONS_MIN}
mae = {h: {"win": [], "loss": []} for h in HORIZONS_MIN}
touch = {t: {"fav": 0, "adv": 0, "none": 0} for t in TOUCH_THRESHOLDS}
matched = 0

for t in trades:
    od = t["open_date"]
    start = pdt(od if "+" in od else od.replace(" ", "T") + "+00:00")
    entry = t["open_rate"]
    short = bool(t["is_short"])
    oc = "win" if t["close_profit"] > 0 else "loss"
    pts = [px for tt, px in series.get(t["pair"], []) if start <= tt <= start + timedelta(minutes=max(HORIZONS_MIN))]
    if len(pts) < 2:
        continue
    matched += 1
    for h in HORIZONS_MIN:
        seg = fwd(t["pair"], start, h)
        if not seg:
            continue
        hi, lo = max(seg), min(seg)
        if short:
            mfe[h][oc].append((entry - lo) / entry * 100)
            mae[h][oc].append((hi - entry) / entry * 100)
        else:
            mfe[h][oc].append((hi - entry) / entry * 100)
            mae[h][oc].append((entry - lo) / entry * 100)
    # first-touch over full max horizon, in chronological order
    full = [px for tt, px in series.get(t["pair"], []) if start <= tt <= start + timedelta(minutes=max(HORIZONS_MIN))]
    for thr in TOUCH_THRESHOLDS:
        hit = "none"
        for px in full:
            mv = ((entry - px) if short else (px - entry)) / entry * 100  # favorable move
            if mv >= thr:
                hit = "fav"; break
            if mv <= -thr:
                hit = "adv"; break
        touch[thr][hit] += 1


def avg(x):
    return sum(x) / len(x) if x else 0.0


print(f"Entries analyzed (forward price available): {matched}\n")
print("=== FORWARD MFE/MAE (no exit applied) ===")
for h in HORIZONS_MIN:
    aw, al = mfe[h]["win"] + mfe[h]["loss"], mae[h]["win"] + mae[h]["loss"]
    print(f"  +{h:3d}min  MFE={avg(aw):.2f}%  MAE={avg(al):.2f}%  ratio={avg(aw)/avg(al) if avg(al) else 0:.2f}")
    print(f"           WIN  MFE={avg(mfe[h]['win']):.2f}% MAE={avg(mae[h]['win']):.2f}%  |  "
          f"LOSS MFE={avg(mfe[h]['loss']):.2f}% MAE={avg(mae[h]['loss']):.2f}%")

print("\n=== FIRST-TOUCH EDGE (favorable vs adverse hit first; ~50% = NO edge) ===")
for thr in TOUCH_THRESHOLDS:
    d = touch[thr]
    decided = d["fav"] + d["adv"]
    rate = d["fav"] / decided * 100 if decided else 0
    print(f"  +/-{thr:.1f}%: favorable-first={d['fav']:3d}  adverse-first={d['adv']:3d}  none={d['none']:3d}  "
          f"-> edge={rate:.1f}%")
