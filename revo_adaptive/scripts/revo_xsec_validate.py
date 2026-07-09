#!/usr/bin/env python3
"""7-day (and ongoing) validation reporter for the xsec momentum paper bot.
Reads the equity log + state, compares forward result vs backtest baseline.
Run: python scripts/revo_xsec_validate.py"""
import json, os
from datetime import datetime, timezone

RT = "/home/fusion_omega/revo_adaptive/user_data/revo_alpha/runtime/bybit"
STATE = f"{RT}/revo_xsec_paper_state.json"
EQLOG = f"{RT}/revo_xsec_paper_equity.jsonl"
START = 1000.0

# Backtest baseline (top-50, L=30, H=7, K=10, net 11bps, 40% cat-stop)
BASE = {"pf": 2.0, "wr": 60.0, "ret_per_reb_pct": 1.0, "monthly_pct": 4.4, "maxdd_pct": 13.0}


def main():
    st = json.load(open(STATE)) if os.path.exists(STATE) else {}
    recs = [json.loads(l) for l in open(EQLOG)] if os.path.exists(EQLOG) else []
    if not recs:
        print("No equity log yet."); return
    reb = [r for r in recs if str(r.get("event", "")).startswith("REBALANCE")]
    stops = [r for r in recs if r.get("event") == "CATASTROPHE_STOP"]
    first_ts = datetime.fromisoformat(recs[0]["ts"]); last_ts = datetime.fromisoformat(recs[-1]["ts"])
    days = (last_ts - first_ts).total_seconds() / 86400
    eq = st.get("equity", START); live = recs[-1].get("live_equity", eq)
    realized_pct = (eq / START - 1) * 100
    live_pct = (live / START - 1) * 100
    n_reb = st.get("rebalance_count", len(reb))
    realized_rebs = max(0, n_reb - 1)   # first rebalance only opens the book

    print("=" * 60)
    print("  XSEC MOMENTUM PAPER BOT — VALIDATION REPORT")
    print("=" * 60)
    print(f"  running         : {days:.1f} days  ({first_ts.date()} -> {last_ts.date()})")
    print(f"  rebalances      : {n_reb}  (realized periods: {realized_rebs})")
    print(f"  catastrophe stop: {len(stops)} triggered")
    print(f"  realized equity : {eq:.2f} USDT  ({realized_pct:+.2f}%)")
    print(f"  live equity MTM : {live:.2f} USDT  ({live_pct:+.2f}%)")
    if realized_rebs >= 1:
        per_reb = realized_pct / realized_rebs
        print(f"  avg/realized reb: {per_reb:+.3f}%   (backtest baseline ~+{BASE['ret_per_reb_pct']}%)")
    print()
    print("  --- BASELINE TO BEAT (backtest, net-of-cost) ---")
    print(f"  PF ~{BASE['pf']}, WR ~{BASE['wr']}%, ~+{BASE['ret_per_reb_pct']}%/rebalance,")
    print(f"  ~+{BASE['monthly_pct']}%/month, maxDD ~{BASE['maxdd_pct']}%")
    print()
    print("  --- GO / NO-GO (needs >=4 realized rebalances ~4 weeks) ---")
    if realized_rebs < 4:
        print(f"  ⏳ Only {realized_rebs} realized rebalance(s). Keep running; re-check at >=4.")
    else:
        per_reb = realized_pct / realized_rebs
        if per_reb >= 0.5:
            print(f"  ✅ GO-ish: +{per_reb:.2f}%/reb sustained. Consider scaling study / live design.")
        elif per_reb >= 0.0:
            print(f"  🟡 MARGINAL: +{per_reb:.2f}%/reb. Edge weaker than backtest; investigate slippage/universe.")
        else:
            print(f"  ❌ NO-GO: {per_reb:.2f}%/reb negative. Forward edge not confirmed; do not go live.")
    print()
    print(f"  current book LONGS : {', '.join(sorted(st.get('longs', {})))}")
    print(f"  current book SHORTS: {', '.join(sorted(st.get('shorts', {})))}")


if __name__ == "__main__":
    main()
