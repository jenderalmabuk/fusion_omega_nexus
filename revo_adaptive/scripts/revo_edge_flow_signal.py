#!/usr/bin/env python3
"""Test the PROPRIETARY flow signal's directional edge at scale.
Uses gate shadow events (direction_engine = flow verdict) vs forward price
from scanner snapshots. First-touch +/-T%; ~50% = flow signal has no edge."""
import json, sqlite3
from datetime import datetime, timedelta
from collections import defaultdict

RT = "/home/fusion_omega/revo_adaptive/user_data/revo_alpha/runtime/bybit"
EV = f"{RT}/revo_gate_shadow_events.jsonl"
PX = f"{RT}/f3a_market_wide_flow_cache.sqlite"
THRESH = [1.5, 2.0, 3.0]
HORIZON_H = 24


def pdt(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


# forward price series per pair
pc = sqlite3.connect(PX)
series = defaultdict(list)
for ts, pair, px in pc.execute("SELECT ts,pair,last_price FROM eligible_pairs WHERE last_price>0 ORDER BY ts"):
    series[pair].append((pdt(ts), float(px)))
pc.close()


def first_touch(pair, start, entry, short, thr):
    end = start + timedelta(hours=HORIZON_H)
    up, dn = entry*(1+thr/100), entry*(1-thr/100)
    for t, px in series.get(pair, []):
        if t < start:
            continue
        if t > end:
            break
        if short:
            if px <= dn: return "fav"
            if px >= up: return "adv"
        else:
            if px >= up: return "fav"
            if px <= dn: return "adv"
    return "none"


res = {("flow_long", t): {"fav": 0, "adv": 0, "none": 0} for t in THRESH}
res.update({("flow_short", t): {"fav": 0, "adv": 0, "none": 0} for t in THRESH})
seen = 0
with open(EV) as fh:
    for line in fh:
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except Exception:
            continue
        de = str(d.get("direction_engine", "")).upper()
        if de not in ("LONG_ONLY", "SHORT_ONLY"):
            continue
        c = d.get("candle")
        if not c:
            continue
        start = pdt(c)
        ser = series.get(d["pair"])
        if not ser:
            continue
        # entry price = first snapshot at/after candle
        entry = next((px for t, px in ser if t >= start), None)
        if entry is None:
            continue
        seen += 1
        short = de == "SHORT_ONLY"
        key = "flow_short" if short else "flow_long"
        for thr in THRESH:
            res[(key, thr)][first_touch(d["pair"], start, entry, short, thr)] += 1

print(f"Flow signals evaluated: {seen}  (horizon={HORIZON_H}h)\n")
print(f"{'signal':12s} {'thr':>4s} {'n':>5s} {'fav':>5s} {'adv':>5s} {'edge%':>6s}")
print("-"*44)
for key in ("flow_long", "flow_short"):
    for thr in THRESH:
        r = res[(key, thr)]
        dec = r["fav"]+r["adv"]
        edge = r["fav"]/dec*100 if dec else 0
        print(f"{key:12s} {thr:4.1f} {dec+r['none']:5d} {r['fav']:5d} {r['adv']:5d} {edge:6.1f}")
# combined
print("-"*44)
for thr in THRESH:
    f = res[("flow_long", thr)]["fav"]+res[("flow_short", thr)]["fav"]
    a = res[("flow_long", thr)]["adv"]+res[("flow_short", thr)]["adv"]
    print(f"{'ALL_flow':12s} {thr:4.1f} {'':5s} {f:5d} {a:5d} {f/(f+a)*100 if (f+a) else 0:6.1f}")
