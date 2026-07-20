"""Liquidity diagnostic: scan ALL universe pairs with 1 cycle of engine logic,
count how many setups pass each gate. Does NOT save state or trigger orders.
Expects Nexus API reachable from inside container."""
import json, os, sys, time, collections, statistics
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Patch fetch_recent
import backtest.data
import bots.nexus_data as nd
backtest.data.fetch_recent = nd.fetch_recent

from clean_core.engine import Engine
from clean_core.imbalance_common import TIERS
from clean_core.imbalance_scheme import recent_setups
from backtest.faithful_imbalance import _filter_liquidity, _filter_ema_dist, _filter_flow, _filter_stochastic
from backtest.data import _drop_forming_bar

UNIVERSE_PATH = os.path.join(os.path.dirname(__file__), "universe.txt")
with open(UNIVERSE_PATH) as f:
    symbols = [l.strip() for l in f if l.strip()]

TIER = "M30"
LTF_TF = TIERS[TIER]["ltf"]      # 5m
ZONE_TF = TIERS[TIER]["zone"]    # H1
FIB_EXPIRY = TIERS[TIER]["fib_expiry"]  # from common
MIN_TURN = 500_000
EMA_DIST = 1.0
STOCH_MAX = 70

print(f"Scanning {len(symbols)} pairs at {TIER} (ltf={LTF_TF}, zone={ZONE_TF})")
print(f"Gates: min_turn={MIN_TURN/1e6:.1f}M ema_dist={EMA_DIST} stoch_max={STOCH_MAX}")

stats = collections.Counter()
funnel = []   # (symbol, setups_raw, after_ema, after_liq, after_stoch, fresh_n, nearest_age)

for i, sym in enumerate(symbols):
    if i % 50 == 0:
        print(f"  progress {i}/{len(symbols)}")
    try:
        zone_df = nd.fetch_recent(sym, ZONE_TF, 260).copy()
        ltf = nd.fetch_recent(sym, LTF_TF, 260).copy()
    except Exception as e:
        stats["fetch_fail"] += 1
        funnel.append((sym, 0, 0, 0, 0, 0, 9999))
        continue

    if zone_df is None or ltf is None or min(len(zone_df), len(ltf)) < 260:
        stats["data_short"] += 1
        funnel.append((sym, 0, 0, 0, 0, 0, 9999))
        continue

    zone_df = _drop_forming_bar(zone_df, ZONE_TF)
    ltf = _drop_forming_bar(ltf, LTF_TF)

    bull = recent_setups(zone_df, ltf, None, "BULL", 3.0, fib_expiry=FIB_EXPIRY)
    bear = recent_setups(zone_df, ltf, None, "BEAR", 3.0, fib_expiry=FIB_EXPIRY)

    raw = len(bull) + len(bear)

    if EMA_DIST > 0:
        bull = _filter_ema_dist(bull, zone_df, EMA_DIST)
        bear = _filter_ema_dist(bear, zone_df, EMA_DIST)
    n_ema = len(bull) + len(bear)

    if MIN_TURN > 0:
        bull = _filter_liquidity(bull, ltf, MIN_TURN)
        bear = _filter_liquidity(bear, ltf, MIN_TURN)
    n_liq = len(bull) + len(bear)

    if STOCH_MAX > 0:
        bull = _filter_stochastic(bull, ltf, "BULL", STOCH_MAX)
        bear = _filter_stochastic(bear, ltf, "BEAR", STOCH_MAX)
    n_stoch = len(bull) + len(bear)

    alls = bull + bear
    n_ltf = len(ltf)
    nearest = 9999
    for s in alls:
        age = n_ltf - 1 - int(s["ce"])
        nearest = min(nearest, age)
    fresh = [s for s in alls if (n_ltf - 1 - int(s["ce"])) <= FIB_EXPIRY]
    n_fresh = len(fresh)

    funnel.append((sym, raw, n_ema, n_liq, n_stoch, n_fresh, nearest))

    if raw > 0:
        stats["raw_setups"] += raw
    if n_fresh > 0:
        stats["fresh"] += n_fresh

# ─── Funnel summary ───
pairs_raw = sum(1 for r in funnel if r[1] > 0)
pairs_fresh = sum(1 for r in funnel if r[5] > 0)
total_raw = sum(r[1] for r in funnel)
total_fresh = sum(r[5] for r in funnel)

print(f"\n{'='*60}")
print(f"FUNNEL (of {len(symbols)} pairs)")
print(f"  raw_setups >0:  {pairs_raw} pairs ({pairs_raw/len(symbols)*100:.0f}%),  {total_raw} setups total")
print(f"  after ema_dist:  {sum(1 for r in funnel if r[2] > 0)} pairs")
print(f"  after liquidity: {sum(1 for r in funnel if r[3] > 0)} pairs ({sum(r[3] for r in funnel)} setups)")
print(f"  after stochastic:{sum(1 for r in funnel if r[4] > 0)} pairs ({sum(r[4] for r in funnel)} setups)")
print(f"  FRESH (<=expiry):{pairs_fresh} pairs ({pairs_fresh/len(symbols)*100:.0f}%),  {total_fresh} setups total")
print(f"  fail-reasons:  data_short={stats['data_short']}  fetch_fail={stats['fetch_fail']}")

# ─── Top pairs by fresh count ───
top = sorted(funnel, key=lambda r: -r[5])[:25]
print(f"\nTOP 25 pairs by fresh setups:")
for sym, raw, ema, liq, st, fsh, near in top:
    print(f"  {sym:14} raw={raw:3} ema={ema:3} liq={liq:3} stoch={st:3} fresh={fsh:3} nearest={near}")

# ─── Liquidity threshold sensitivity ───
# For every pair with >0 raw setups, test: would PASS min_turn=500k?
# And what % of pairs fail liquidity at 250k / 500k / 1M / 2M / 5M?
thresholds = [250_000, 500_000, 1_000_000, 2_000_000, 5_000_000]
print(f"\n{'='*60}")
print("LIQUIDITY THRESHOLD SENSITIVITY (per-pair, among pairs with >0 raw setups)")
print("Threshold | pairs_liquid | %_of_total_universe")
pairs_with_raw = [r for r in funnel if r[1] > 0]
for th in thresholds:
    liq_count = 0
    for sym, raw, ema, lq, st, fsh, near in pairs_with_raw:
        # Re-run liquidity check for this pair outside the funnel loop (cache-safe)
        try:
            ltf_df = nd.fetch_recent(sym, LTF_TF, 260)
        except:
            continue
        if ltf_df is None or len(ltf_df) < 260:
            continue
        ltf_df = _drop_forming_bar(ltf_df, LTF_TF)
        turn = ltf_df["volume"].to_numpy() * ltf_df["close"].to_numpy()
        recent_turn = float(turn[-20:].sum())
        if recent_turn >= th:
            liq_count += 1
    pct = liq_count / len(symbols) * 100
    print(f"  {th/1e6:4.1f}M        | {liq_count:5d}        | {pct:5.1f}%")

# ─── Turnover distribution (top 50 by 20-bar turnover) ───
turnovers = []
for sym in [r[0] for r in funnel[:50]]:
    try:
        ltf_df = nd.fetch_recent(sym, LTF_TF, 100)
        if ltf_df is not None and len(ltf_df) >= 20:
            ltf_df = _drop_forming_bar(ltf_df, LTF_TF)
            if len(ltf_df) >= 20:
                turn = float(ltf_df["volume"].to_numpy() * ltf_df["close"].to_numpy())[-20:].sum()
                turnovers.append((sym, turn))
    except:
        pass
print(f"\nACTUAL 20-bar turnover sample (top pairs):")
for sym, t in sorted(turnovers, key=lambda x: -x[1])[:20]:
    print(f"  {sym:14}  {t/1e6:.1f}M")