#!/usr/bin/env python3
"""Side-by-side monitor for the three cross-sectional PAPER strategies (XSEC / CVD / BLEND).

Reads each strategy's equity log + state and prints one compact comparison: realized & live
equity, since-inception %, rebalances, catastrophe-stops, live daily Sharpe, live-equity maxDD,
and current basket sizes. Stdlib-only.

Usage:
  python3 scripts/revo_paper_compare.py            # one snapshot
  watch -n 30 'python3 scripts/revo_paper_compare.py'   # live monitor (replaces manual tail/cat)
  python3 scripts/revo_paper_compare.py --baskets  # also print current LONG/SHORT lists
"""
import json, sys, os
from datetime import datetime, timezone

RT = "/home/fusion_omega/revo_adaptive/user_data/revo_alpha/runtime/bybit"
START = 1000.0
STRATS = [
    ("XSEC",  f"{RT}/revo_xsec_paper_equity.jsonl",  f"{RT}/revo_xsec_paper_state.json"),
    ("CVD",   f"{RT}/revo_cvd_paper_equity.jsonl",   f"{RT}/revo_cvd_paper_state.json"),
    ("BLEND", f"{RT}/revo_blend_paper_equity.jsonl", f"{RT}/revo_blend_paper_state.json"),
    ("XSEC_VT", f"{RT}/revo_xsec_vt_paper_equity.jsonl", f"{RT}/revo_xsec_vt_paper_state.json"),
    ("FUND", f"{RT}/revo_fund_paper_equity.jsonl", f"{RT}/revo_fund_paper_state.json"),
]


def _rows(f):
    if not os.path.exists(f):
        return []
    out = []
    for ln in open(f):
        ln = ln.strip()
        if ln:
            try:
                out.append(json.loads(ln))
            except Exception:
                pass
    return out


def _daily_sharpe(rows):
    """Annualized Sharpe from last live_equity per calendar day."""
    by_day = {}
    for r in rows:
        le = r.get("live_equity")
        if le:
            by_day[r["ts"][:10]] = le          # last value of the day wins (rows are chronological)
    eq = [by_day[d] for d in sorted(by_day)]
    if len(eq) < 3:
        return None
    rets = [eq[i] / eq[i - 1] - 1 for i in range(1, len(eq)) if eq[i - 1] > 0]
    if len(rets) < 2:
        return None
    m = sum(rets) / len(rets)
    sd = (sum((x - m) ** 2 for x in rets) / len(rets)) ** 0.5
    if sd == 0:
        return None
    return m / sd * (365 ** 0.5)


def _maxdd(rows):
    le = [r.get("live_equity") for r in rows if r.get("live_equity")]
    if not le:
        return 0.0
    peak = le[0]; mdd = 0.0
    for x in le:
        peak = max(peak, x); mdd = min(mdd, (x / peak - 1) * 100)
    return mdd


def main():
    show_baskets = "--baskets" in sys.argv
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
    print("=" * 92)
    print(f"PAPER CROSS-SECTIONAL STRATEGIES — COMPARISON  |  {now}  (SHADOW, no real orders)")
    print("=" * 92)
    hdr = f"{'strat':<6}{'realized':>11}{'since%':>9}{'liveMTM':>11}{'unreal%':>9}{'reb':>5}{'stops':>6}{'dSharpe':>9}{'maxDD%':>8}{'L/S':>7}"
    print(hdr); print("-" * len(hdr))
    baskets = []
    for name, eqf, stf in STRATS:
        rows = _rows(eqf)
        st = json.load(open(stf)) if os.path.exists(stf) else {}
        if not rows and not st:
            print(f"{name:<6}{'(not started)':>20}")
            continue
        realized = st.get("equity", START)
        last = rows[-1] if rows else {}
        live = last.get("live_equity", realized)
        unreal = last.get("unrealized_pct", 0.0)
        reb = st.get("rebalance_count", 0)
        stops = sum(1 for r in rows if str(r.get("event", "")).startswith("CATASTROPHE"))
        sharpe = _daily_sharpe(rows)
        mdd = _maxdd(rows)
        nl = last.get("n_long", len(st.get("longs", {}))); ns = last.get("n_short", len(st.get("shorts", {})))
        since = (realized / START - 1) * 100
        sh = f"{sharpe:+.2f}" if sharpe is not None else "  n/a"
        print(f"{name:<6}{realized:>11.2f}{since:>+9.2f}{live:>11.2f}{unreal:>+9.2f}"
              f"{reb:>5}{stops:>6}{sh:>9}{mdd:>8.1f}{f'{nl}/{ns}':>7}")
        baskets.append((name, st.get("longs", {}), st.get("shorts", {})))

    # --- 4th arm: fusion daily engine (different repo/schema: inverse-vol + vol-target/Kelly, DAILY) ---
    fstate = "/home/fusion_omega/fusion/engine/state/paper_state.json"
    if os.path.exists(fstate):
        try:
            fs = json.load(open(fstate))
            fstart = 10000.0
            eq = fs.get("equity", fstart); peak = fs.get("peak", eq)
            since = (eq / fstart - 1) * 100
            hist = fs.get("history", [])
            # daily Sharpe + maxDD from history equity
            eqs = [h["equity"] for h in hist if h.get("equity")]
            sh = None
            if len(eqs) >= 3:
                rr = [eqs[i] / eqs[i - 1] - 1 for i in range(1, len(eqs)) if eqs[i - 1] > 0]
                if len(rr) >= 2:
                    m = sum(rr) / len(rr); sd = (sum((x - m) ** 2 for x in rr) / len(rr)) ** 0.5
                    sh = m / sd * (365 ** 0.5) if sd else None
            mdd = 0.0; pk = eqs[0] if eqs else eq
            for x in eqs:
                pk = max(pk, x); mdd = min(mdd, (x / pk - 1) * 100)
            pos = fs.get("positions", {})
            nl = sum(1 for p in pos.values() if p.get("notional", 0) > 0)
            ns = sum(1 for p in pos.values() if p.get("notional", 0) < 0)
            shs = f"{sh:+.2f}" if sh is not None else "  n/a"
            halted = "  [HALT]" if fs.get("halted") else ""
            print(f"{'FUSd':<6}{eq:>11.2f}{since:>+9.2f}{eq:>11.2f}{0.0:>+9.2f}"
                  f"{fs.get('rebalances', 0):>5}{'—':>6}{shs:>9}{mdd:>8.1f}{f'{nl}/{ns}':>7}{halted}")
            baskets.append(("FUSd(daily)",
                            {s: 1 for s, p in pos.items() if p.get("notional", 0) > 0},
                            {s: 1 for s, p in pos.items() if p.get("notional", 0) < 0}))
        except Exception as e:
            print(f"{'FUSd':<6}(read err: {e})")

    print("-" * len(hdr))
    print("realized=locked equity | liveMTM=incl. open positions | dSharpe=annualized daily Sharpe (live)")
    print("FUSd = fusion DAILY engine ($10k base, inverse-vol + vol-target/Kelly); others weekly ($1k base).")
    print("Note: very early (few rebalances) — judge after 4-8 rebalances vs backtest (XSEC 2.06 / CVD 2.66 / BLEND 2.38).")
    if show_baskets:
        for name, longs, shorts in baskets:
            print(f"\n[{name}]")
            print("  LONG :", ", ".join(sorted(s.replace("USDT", "") for s in longs)) or "-")
            print("  SHORT:", ", ".join(sorted(s.replace("USDT", "") for s in shorts)) or "-")


if __name__ == "__main__":
    main()
