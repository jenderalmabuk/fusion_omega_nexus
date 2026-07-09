#!/usr/bin/env python3
"""Shadow analysis of FLOW_TRAP_RISK: does the trap flag predict reversal (correct block)
or does price continue (over-aggressive block)? Uses logged gate decisions + Bybit forward price."""
import json, time, urllib.request, urllib.parse
from datetime import datetime, timezone
from collections import defaultdict

HB = "/home/fusion_omega/revo_adaptive/user_data/revo_alpha/runtime/bybit/revo_gate_heartbeat_events.jsonl"

def bybit_klines(symbol, interval="15", limit=1000):
    url = "https://api.bybit.com/v5/market/kline?" + urllib.parse.urlencode(
        {"category": "linear", "symbol": symbol, "interval": interval, "limit": limit})
    req = urllib.request.Request(url, headers={"User-Agent": "trap-shadow"})
    with urllib.request.urlopen(req, timeout=20) as r:
        d = json.load(r)
    rows = d.get("result", {}).get("list", []) or []
    # Bybit returns newest-first: [start_ms, open, high, low, close, volume, turnover]
    out = [(int(x[0]), float(x[4])) for x in rows]  # (start_ms, close)
    out.sort()
    return out

# 1) collect deduped trap episodes
ev = []
for line in open(HB):
    line = line.strip()
    if not line:
        continue
    try:
        d = json.loads(line)
        q = str(d.get("flow_quadrant", ""))
        if "TRAP" in q and "DIVERGENCE" in q:
            ts = d.get("ts")
            ms = int(datetime.fromisoformat(ts).timestamp() * 1000)
            ev.append((d.get("pair"), ms, "BULL" if "BULL" in q else "BEAR", ts[:13]))
    except Exception:
        pass
# dedupe (pair, hour, type)
seen = set(); episodes = []
for p, ms, t, hr in ev:
    k = (p, hr, t)
    if k not in seen:
        seen.add(k); episodes.append((p, ms, t))

pairs = sorted(set(e[0] for e in episodes))
print(f"episodes={len(episodes)} pairs={len(pairs)}")

# 2) fetch klines per pair
kl = {}
for i, p in enumerate(pairs):
    sym = p.replace("/USDT:USDT", "USDT")
    try:
        kl[p] = bybit_klines(sym)
    except Exception as e:
        kl[p] = []
    time.sleep(0.12)
    if (i + 1) % 40 == 0:
        print(f"  fetched {i+1}/{len(pairs)}")

# 3) forward returns at each episode
import bisect
def fwd_ret(closes_ms, t_ms, horizon):
    starts = [c[0] for c in closes_ms]
    idx = bisect.bisect_right(starts, t_ms) - 1
    if idx < 0 or idx + horizon >= len(closes_ms):
        return None
    c0 = closes_ms[idx][1]; c1 = closes_ms[idx + horizon][1]
    if c0 <= 0:
        return None
    return (c1 - c0) / c0 * 100.0

res = {("BULL", 4): [], ("BULL", 16): [], ("BEAR", 4): [], ("BEAR", 16): []}
for p, ms, t in episodes:
    data = kl.get(p) or []
    if len(data) < 20:
        continue
    for h in (4, 16):
        r = fwd_ret(data, ms, h)
        if r is not None:
            res[(t, h)].append(r)

def stats(xs):
    if not xs:
        return "n=0"
    import statistics
    xs2 = sorted(xs)
    mean = sum(xs)/len(xs)
    med = statistics.median(xs)
    return f"n={len(xs)} mean={mean:+.3f}% median={med:+.3f}%"

print("\n=== FORWARD RETURN AFTER TRAP FLAG (price direction) ===")
print("BULL_TRAP (correct if price FALLS -> negative):")
print("  +1h :", stats(res[("BULL", 4)]))
print("  +4h :", stats(res[("BULL", 16)]))
print("BEAR_TRAP (correct if price RISES -> positive):")
print("  +1h :", stats(res[("BEAR", 4)]))
print("  +4h :", stats(res[("BEAR", 16)]))

# 4) "trap correct" rate
def correct_rate(xs, want_negative):
    if not xs:
        return "n=0"
    if want_negative:
        c = sum(1 for x in xs if x < 0)
    else:
        c = sum(1 for x in xs if x > 0)
    return f"{100*c/len(xs):.1f}% ({c}/{len(xs)})"

print("\n=== TRAP CORRECT RATE (did price move trap-implied direction?) ===")
print("  BULL_TRAP reversed down +4h:", correct_rate(res[("BULL", 16)], True))
print("  BEAR_TRAP reversed up   +4h:", correct_rate(res[("BEAR", 16)], False))
