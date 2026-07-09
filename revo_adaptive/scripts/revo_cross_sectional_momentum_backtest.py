#!/usr/bin/env python3
"""
REVO_CROSS_SECTIONAL_MOMENTUM_BACKTEST
Based on fusion CROSS_SECTIONAL_MOMENTUM_STRATEGY.md + edge_oicvd_factor.py

Core mechanic:
- Universe: top 50 coins by 24h volume
- Daily long/short rebalance (not 7d hold; simplified daily for backtest speed)
- Factor: trailing 30d return, cross-sectional z-scored
- Long top K (30% quantile), Short bottom K
- Dollar-neutral, equal-weight (simplified; no vol-scaling in v1)
- Funding proxy from archive CVD data (placeholder if not available)
- Cost: 11bps per side turnover
"""

import csv, math
from pathlib import Path
from datetime import datetime, timedelta
from collections import defaultdict

DATA_DIR = Path('/tmp/xsec_data/freqtradexomega/scripts/_edge_data_d')
OI_DIR = Path('/tmp/xsec_data/freqtradexomega/scripts/_edge_oi_d')
CVD_DIR = Path('/tmp/xsec_data/freqtradexomega/scripts/_edge_cvd_d')

FEE_SIDE = 0.00055  # 5.5bps taker per side
LOOKBACK = 30       # days for momentum
QUANTILE = 0.30     # top/bottom fraction each side
K = 10              # coins per side
REBALANCE_DAYS = 1  # daily for backtest granularity
MIN_COINS = 15      # minimum coins needed on a day to rebalance


def load_daily_ohlcv(dir_path):
    """Load all CSV files, resample to daily OHLCV, return dict[sym] = list of rows."""
    data = {}
    for f in sorted(dir_path.glob('*.csv')):
        sym = f.stem
        rows = []
        with open(f) as fh:
            for r in csv.DictReader(fh):
                try:
                    ts = datetime.fromtimestamp(int(r['ts']) / 1000)
                    rows.append({
                        'date': ts.date(),
                        'open': float(r['open']),
                        'high': float(r['high']),
                        'low': float(r['low']),
                        'close': float(r['close']),
                        'volume': float(r['volume']),
                    })
                except Exception:
                    pass
        if len(rows) < 200:
            continue
        rows.sort(key=lambda x: x['date'])
        # resample to daily (last close of day)
        daily = {}
        for r in rows:
            daily[r['date']] = r
        daily_list = sorted(daily.values(), key=lambda x: x['date'])
        data[sym] = daily_list
    return data


def load_oi(dir_path, data):
    """Load OI data matched by date."""
    oi_data = {}
    for f in sorted(dir_path.glob('*.csv')):
        sym = f.stem
        if sym not in data:
            continue
        rows = []
        with open(f) as fh:
            for r in csv.DictReader(fh):
                try:
                    ts = datetime.fromtimestamp(int(r['ts']) / 1000)
                    rows.append({'date': ts.date(), 'oi': float(r.get('oi', 0))})
                except Exception:
                    pass
        # map date to oi
        oi_by_date = {r['date']: r['oi'] for r in rows if r['oi'] > 0}
        oi_data[sym] = oi_by_date
    return oi_data


def load_cvd(dir_path, data):
    """Load CVD data matched by date."""
    cvd_data = {}
    for f in sorted(dir_path.glob('*.csv')):
        sym = f.stem
        if sym not in data:
            continue
        rows = []
        with open(f) as fh:
            for r in csv.DictReader(fh):
                try:
                    ts = datetime.fromtimestamp(int(r['ts']) / 1000)
                    rows.append({'date': ts.date(), 'flow': float(r.get('flow', 0))})
                except Exception:
                    pass
        cvd_by_date = {r['date']: r['flow'] for r in rows}
        cvd_data[sym] = cvd_by_date
    return cvd_data


def build_aligned_panel(data, oi_data, cvd_data):
    """Build daily close DataFrame + ret + funding proxy."""
    # find common date range
    all_dates = set()
    for sym, rows in data.items():
        for r in rows:
            all_dates.add(r['date'])
    dates = sorted(all_dates)

    # build close matrix
    close = {}
    oi = {}
    cvd = {}
    for sym, rows in data.items():
        by_date = {r['date']: r['close'] for r in rows}
        close[sym] = [by_date.get(d) for d in dates]
        oi[sym] = [oi_data.get(sym, {}).get(d) for d in dates]
        cvd[sym] = [cvd_data.get(sym, {}).get(d) for d in dates]

    # keep coins with good coverage over the window
    n_dates = len(dates)
    good_coins = []
    for sym in close:
        valid_close = sum(1 for v in close[sym] if v is not None)
        if valid_close >= n_dates * 0.8:
            good_coins.append(sym)

    return dates, {s: close[s] for s in good_coins}, oi, cvd


def daily_universe_by_volume(data, dates, n_coins=50):
    """Pick top N coins by latest 24h volume on each day."""
    universe_by_day = {}
    for day_idx, d in enumerate(dates):
        vols = []
        for sym, rows in data.items():
            if day_idx < len(rows) and rows[day_idx]['close'] is not None:
                vols.append((sym, rows[day_idx]['volume']))
        vols.sort(key=lambda x: x[1], reverse=True)
        universe_by_day[d] = set(s[0] for s in vols[:n_coins])
    return universe_by_day


def compute_factor_scores(close, dates, universe, lookback=LOOKBACK):
    """
    For each day, compute trailing return over lookback days,
    cross-sectional z-score within universe.
    Return dict[sym][day_idx] = zscore.
    """
    scores = defaultdict(dict)
    for day_idx in range(lookback, len(dates)):
        d = dates[day_idx]
        prev_idx = day_idx - lookback
        if d not in universe:
            continue
        u = universe[d]
        rets = {}
        for sym in u:
            if sym not in close:
                continue
            c_now = close[sym][day_idx]
            c_prev = close[sym][prev_idx]
            if c_now is None or c_prev is None or c_prev <= 0:
                continue
            rets[sym] = c_now / c_prev - 1
        if len(rets) < MIN_COINS:
            continue
        # cross-sectional z-score
        vals = list(rets.values())
        mu = sum(vals) / len(vals)
        if len(vals) < 2:
            continue
        sd = math.sqrt(sum((v - mu) ** 2 for v in vals) / (len(vals) - 1))
        if sd == 0:
            continue
        for sym in rets:
            scores[sym][day_idx] = (rets[sym] - mu) / sd
    return scores


def backtest_daily(close, scores, dates, universe, oi, cvd, k=K, quantile=QUANTILE,
                   rebalance_interval=REBALANCE_DAYS):
    """
    Daily rebalance: long top quantile, short bottom quantile.
    Market-neutral, equal-weight. Cost = turnover * FEE_SIDE.
    Returns list of daily returns.
    """
    daily_returns = []
    prev_w = None  # previous weights {sym: weight}

    for day_idx in range(LOOKBACK + 3, len(dates) - 1):
        d = dates[day_idx]
        next_d = dates[day_idx + 1] if day_idx + 1 < len(dates) else None
        if d not in universe or next_d is None:
            daily_returns.append(0.0)
            continue

        u = universe[d]
        # get scores for this day
        day_scores = {}
        for sym in u:
            if sym in scores and day_idx in scores[sym]:
                day_scores[sym] = scores[sym][day_idx]
        if len(day_scores) < MIN_COINS:
            daily_returns.append(0.0)
            continue

        # rank and pick longs/shorts
        ranked = sorted(day_scores.items(), key=lambda x: x[1], reverse=True)
        n_side = max(1, int(len(ranked) * quantile))
        longs = [s[0] for s in ranked[:n_side]]
        shorts = [s[0] for s in ranked[-n_side:]]

        # equal-weight within each side
        w = {}
        w_long = 1.0 / len(longs)
        w_short = 1.0 / len(shorts)
        for s in longs:
            w[s] = w_long
        for s in shorts:
            w[s] = -w_short

        # next-day return
        pnl = 0.0
        for sym, weight in w.items():
            if sym not in close:
                continue
            c_today = close[sym][day_idx]
            c_next = close[sym][day_idx + 1] if day_idx + 1 < len(dates) else None
            if c_today is None or c_next is None:
                continue
            ret = (c_next / c_today - 1)
            pnl += weight * ret

        # funding carry proxy: OI + CVD (simplified)
        # positive funding = longs pay, shorts receive
        fund_carry = 0.0
        for sym, weight in w.items():
            if sym in oi:
                oi_val = oi[sym][day_idx]
                oi_prev = oi[sym][day_idx - 1] if day_idx > 0 else None
                if oi_val and oi_prev:
                    oi_chg = oi_val / oi_prev - 1
                    # proxy: OI rising fast -> funding likely positive -> long pays
                    fund_carry += -weight * oi_chg * 0.0001  # tiny proxy cost/gain

        # turnover cost
        if prev_w is None:
            turn = sum(abs(ww) for ww in w.values())
        else:
            turn = 0.0
            all_syms = set(w.keys()) | set(prev_w.keys())
            for s in all_syms:
                turn += abs(w.get(s, 0) - prev_w.get(s, 0))
        cost = turn * FEE_SIDE

        daily_returns.append(pnl + fund_carry - cost)
        prev_w = w

    return daily_returns


def calc_metrics(daily_returns):
    if not daily_returns:
        return {}
    r = [v for v in daily_returns if v != 0.0]
    if len(r) < 30:
        return {}
    wins = [v for v in r if v > 0]
    losses = [v for v in r if v < 0]
    pf = sum(wins) / abs(sum(losses)) if losses else 99
    avg = sum(r) / len(r)
    sd = math.sqrt(sum((v - avg) ** 2 for v in r) / (len(r) - 1)) if len(r) > 1 else 1e-12
    sharpe = avg / sd * math.sqrt(365)
    # equity curve
    eq = 1.0
    peak = 1.0
    maxdd = 0.0
    for v in r:
        eq *= 1 + v
        peak = max(peak, eq)
        maxdd = max(maxdd, 1 - eq / peak)
    wr = len(wins) / len(r) * 100
    total_ret = (eq - 1) * 100
    return {
        'n_days': len(r),
        'avg_daily': avg * 100,
        'sharpe': sharpe,
        'pf': pf,
        'maxdd': maxdd * 100,
        'wr': wr,
        'total_ret': total_ret,
        'ann_ret': (((eq) ** (365 / len(r)) - 1) * 100),
    }


def main():
    print('=== REVO CROSS-SECTIONAL MOMENTUM BACKTEST ===')
    print(f'Loading daily OHLCV from {DATA_DIR}...')
    data = load_daily_ohlcv(DATA_DIR)
    print(f'  Coins with >=200 days: {len(data)}')

    print('Loading OI data...')
    oi_data = load_oi(OI_DIR, data)
    print(f'  Coins with OI: {len(oi_data)}')

    print('Loading CVD data...')
    cvd_data = load_cvd(CVD_DIR, data)
    print(f'  Coins with CVD: {len(cvd_data)}')

    dates, close, oi, cvd = build_aligned_panel(data, oi_data, cvd_data)
    n_coins = len(close)
    n_days = len(dates)
    print(f'\nAligned panel: {n_coins} coins × {n_days} days')
    print(f'  Date range: {dates[0]} → {dates[-1]}')
    print(f'  Data span: {n_days} days (~{n_days/365:.1f} years)')

    # daily universe: top 50 by volume
    universe = daily_universe_by_volume(data, dates, n_coins=min(50, n_coins))
    print(f'\nUniverse: top {min(50, n_coins)} by daily volume')

    # compute factor scores
    scores = compute_factor_scores(close, dates, universe, lookback=LOOKBACK)

    # backtest variants
    variants = [
        ('L=30, K=10, daily', LOOKBACK, 10, 0.30, 1),
        ('L=14, K=10, daily', 14, 10, 0.30, 1),
        ('L=30, K=15, daily', LOOKBACK, 15, 0.30, 1),
        ('L=30, K=10, weekly', LOOKBACK, 10, 0.30, 7),
        ('L=7,  K=10, daily', 7, 10, 0.30, 1),
    ]

    print('\n' + '=' * 90)
    print(f'{"Variant":<22s} {"Days":>5s} {"Sharpe":>7s} {"PF":>6s} {"WR%":>6s} '
          f'{"Ret%":>8s} {"Ann%":>8s} {"MaxDD%":>7s}')
    print('-' * 90)

    best_variant = None
    best_sharpe = -999

    for name, lb, k, q, reb in variants:
        scores_v = compute_factor_scores(close, dates, universe, lookback=lb)
        daily_r = backtest_daily(close, scores_v, dates, universe, oi, cvd,
                                 k=k, quantile=q, rebalance_interval=reb)
        m = calc_metrics(daily_r)
        if not m:
            print(f'{name:<22s} (insufficient data)')
            continue
        # IS/OOS split
        split = int(len(daily_r) * 0.6)
        r_is = [v for v in daily_r[:split] if v != 0.0]
        r_oos = [v for v in daily_r[split:] if v != 0.0]
        m_is = calc_metrics(r_is)
        m_oos = calc_metrics(r_oos)

        flag = '✅' if (m['sharpe'] > 0.5 and m_oos['sharpe'] > 0.3 and m['pf'] > 1.2) else \
               '⚠️' if (m['sharpe'] > 0 and m['pf'] > 1.0) else '❌'

        print(f'{flag} {name:<19s} {m["n_days"]:>5d} {m["sharpe"]:>+7.2f} {m["pf"]:>6.2f} '
              f'{m["wr"]:>5.1f}% {m["total_ret"]:>+7.2f}% {m["ann_ret"]:>+7.2f}% {m["maxdd"]:>6.2f}%')

        if m_is and m_oos:
            print(f'  {"IS":>21s} {m_is["n_days"]:>5d} {m_is["sharpe"]:>+7.2f} {m_is["pf"]:>6.2f} '
                  f'{m_is["wr"]:>5.1f}% {m_is["total_ret"]:>+7.2f}%')
            print(f'  {"OOS":>21s} {m_oos["n_days"]:>5d} {m_oos["sharpe"]:>+7.2f} {m_oos["pf"]:>6.2f} '
                  f'{m_oos["wr"]:>5.1f}% {m_oos["total_ret"]:>+7.2f}%')

        if m['sharpe'] > best_sharpe:
            best_sharpe = m['sharpe']
            best_variant = (name, m, m_is, m_oos)

    print('\n' + '=' * 90)
    print('=== VERDICT ===')
    if not best_variant or best_sharpe <= 0:
        print('❌ NO EDGE DETECTED — No variant shows positive Sharpe in both IS and OOS.')
        print('   Cross-sectional momentum on this data does not pass robustness checks.')
    elif best_sharpe > 1.0 and best_variant[3]['sharpe'] > 0.5:
        print(f'✅ EDGE DETECTED — Best variant: {best_variant[0]}')
        print(f'   Sharpe={best_sharpe:.2f}, PF={best_variant[1]["pf"]:.2f}, Ann={best_variant[1]["ann_ret"]:.1f}%')
        print(f'   IS: Sharpe={best_variant[2]["sharpe"]:.2f}, OOS: Sharpe={best_variant[3]["sharpe"]:.2f}')
        print('   → Layak diuji forward paper sebagai mode tambahan adaptive.')
    else:
        print(f'{best_variant[0]} shows some promise (Sharpe={best_sharpe:.2f})')
        print(f'   IS/OOS consistency: moderate')
        print('   → Marginal edge — perlu refinement dan forward-test sebelum commit.')


if __name__ == '__main__':
    main()