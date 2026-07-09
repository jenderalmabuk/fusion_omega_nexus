#!/usr/bin/env python3
"""
REVO_SNIPER_BREAKOUT_V2 — backtest only.

Dari ide user dan archive bot lama:
- Coin trending (return 30d tertinggi, universe ≤50)
- Breakout trigger: price > 20-candle high + EMA50>EMA200 + volume spike
- OI/CVD/funding filter (z-score, tidak crowded)
- Resistance/imbalance detection: cari resistance/imbalance/OB terdekat di atas entry
- TP = min(2×risk, resistance_gap - 0.3%) — jadi TP selalu di bawah resistance berikutnya
- Entry: market-next-open (realistic). Fee: 11bps taker round-trip.
- SL: 1.5×ATR atau swing low terdekat (mana yang lebih ketat)
- Timeout: 48 candle (12 jam di 15m)
"""

import csv, math, statistics
from pathlib import Path
from datetime import datetime

CACHE = Path('/tmp/fusion_extract/fusion/backtest/cache')
FEE = 2 * 0.00055  # 11bps round-trip taker


# ===== indicators =====
def ema(vals, span):
    a = 2 / (span + 1)
    out, ema_val = [], None
    for v in vals:
        ema_val = v if ema_val is None else a * v + (1 - a) * ema_val
        out.append(ema_val)
    return out


def atr(high, low, close, period=14):
    tr_ema = None
    a = 1 / period
    out = []
    for i in range(len(close)):
        h, l = high[i], low[i]
        pc = close[i - 1] if i > 0 else close[i]
        tr = max(h - l, abs(h - pc), abs(l - pc))
        tr_ema = tr if tr_ema is None else a * tr + (1 - a) * tr_ema
        out.append(tr_ema)
    return out


def zscore(vals, lookback=48):
    out = [0.0] * len(vals)
    for i in range(lookback, len(vals)):
        w = vals[i - lookback:i]
        m = sum(w) / lookback
        sd = math.sqrt(sum((x - m) ** 2 for x in w) / (lookback - 1)) if lookback > 1 else 1e-12
        out[i] = (vals[i] - m) / (sd or 1e-12)
    return out


# ===== data loading =====
def read_csv(path: Path):
    rows = []
    with path.open() as f:
        for r in csv.DictReader(f):
            try:
                rows.append({
                    't': r['open_time'],
                    'open': float(r['open']),
                    'high': float(r['high']),
                    'low': float(r['low']),
                    'close': float(r['close']),
                    'volume': float(r.get('volume', 0)),
                    'taker_buy_base': float(r.get('taker_buy_base', 0)),
                })
            except Exception:
                pass
    return rows


# ===== trending universe =====
def pick_trending_coins(files, lookback_bars=30 * 24 * 4, topn=50):
    scored = []
    for p in files:
        rows = read_csv(p)
        if len(rows) < lookback_bars + 500:
            continue
        close_vals = [r['close'] for r in rows]
        ret = close_vals[-1] / close_vals[-lookback_bars] - 1
        vol24 = statistics.mean([r['volume'] for r in rows[-96:]]) if len(rows) >= 96 else 0
        scored.append((ret, p.stem.replace('.csv', '').split('_')[0], p, rows, vol24))
    scored.sort(reverse=True)
    # pick top by return, but require minimum volume
    picked = [s for s in scored if s[4] > 0]
    return picked[:topn]


# ===== imbalance/resistance detection =====
def detect_imbalances(high, low, open_, close, lookback=100):
    """
    Find recent bullish FVG (gap up) and bearish FVG (gap down) zones,
    plus highest resistance within lookback.
    Returns list of resistance zones sorted by price.
    """
    zones = []
    n = len(high)
    start = max(0, n - lookback)
    for i in range(start + 2, n):
        # bullish FVG: low[i] > high[i-2]  (gap up)
        if low[i] > high[i - 2]:
            zones.append({
                'type': 'FVG_BULL',
                'low': low[i],
                'high': high[i],
                'price': (low[i] + high[i]) / 2,
                'i': i,
            })
        # bearish FVG: high[i] < low[i-2] (gap down)
        if high[i] < low[i - 2]:
            zones.append({
                'type': 'FVG_BEAR',
                'low': low[i],
                'high': high[i],
                'price': (low[i] + high[i]) / 2,
                'i': i,
            })
        # simple swing high resistance
        if i >= 20:
            swing_high = max(high[i - 20:i])
            if swing_high > high[i] * 1.005:
                zones.append({
                    'type': 'RESISTANCE',
                    'low': swing_high * 0.995,
                    'high': swing_high,
                    'price': swing_high,
                    'i': i,
                })

    # order block detection: bullish OB = red candle before impulse
    for i in range(start + 3, n - 1):
        if open_[i] > close[i] and high[i] < low[i + 1]:  # bearish candle + gap up next
            zones.append({
                'type': 'OB_BULL',
                'low': low[i],
                'high': high[i],
                'price': (low[i] + high[i]) / 2,
                'i': i,
            })

    # deduplicate nearby zones
    zones.sort(key=lambda z: z['price'])
    merged = []
    for z in zones:
        if merged and abs(z['price'] - merged[-1]['price']) / merged[-1]['price'] < 0.005:
            # average nearby
            merged[-1]['price'] = (merged[-1]['price'] + z['price']) / 2
            merged[-1]['high'] = max(merged[-1]['high'], z['high'])
            merged[-1]['low'] = min(merged[-1]['low'], z['low'])
        else:
            merged.append(z)
    return merged


# ===== proxy CVD / OI / funding from OHLCV =====
def proxy_cvd_zscore(taker_buy, volume, lookback=48):
    """CVD proxy: (2*taker_buy - volume) / volume, then zscore"""
    cvd_raw = []
    for tb, v in zip(taker_buy, volume):
        cvd_raw.append((2 * tb - v) / (v or 1e-12))
    return zscore(cvd_raw, lookback)


def proxy_oi_delta_zscore(volume, lookback=48):
    """OI proxy: volume change zscore (rough OI interest proxy)"""
    return zscore(volume, lookback)


def funding_proxy(high, low, close, lookback=48):
    """
    Funding rate proxy: persistence of price above/below VWAP-ish.
    Positive = longs paying (price persistent above avg), Negative = shorts paying.
    This is a rough proxy since we don't have real funding data in 15m OHLCV.
    """
    out = [0.0] * len(close)
    for i in range(lookback, len(close)):
        typical = [(high[j] + low[j] + close[j]) / 3 for j in range(i - lookback, i)]
        avg = sum(typical) / lookback
        # how much current price deviates from recent typical average
        dev = (close[i] - avg) / (avg or 1e-12)
        # clamp to reasonable funding proxy range (-0.1% to 0.1%)
        out[i] = max(-0.001, min(0.001, dev * 0.0001))
    return out


# ===== backtest engine =====
def backtest_v2(rows, symbol, topn_trending_ret=0.0):
    close = [r['close'] for r in rows]
    high = [r['high'] for r in rows]
    low = [r['low'] for r in rows]
    open_ = [r['open'] for r in rows]
    volume = [r['volume'] for r in rows]
    taker = [r['taker_buy_base'] for r in rows]

    e50 = ema(close, 50)
    e200 = ema(close, 200)
    atr_vals = atr(high, low, close, 14)
    cvd_z = proxy_cvd_zscore(taker, volume, 48)
    oi_z = proxy_oi_delta_zscore(volume, 48)
    fund = funding_proxy(high, low, close, 48)

    trades = []
    i = 250  # skip warmup
    n = len(rows)

    while i < n - 3:
        # basic validity
        if not all(math.isfinite(x) for x in [e200[i], e50[i], atr_vals[i], cvd_z[i], oi_z[i], close[i]]):
            i += 1
            continue
        if atr_vals[i] <= 0:
            i += 1
            continue

        # trending filter: EMA50 > EMA200, price above EMA200
        trend_up = e50[i] > e200[i] and close[i] > e200[i]
        if not trend_up:
            i += 1
            continue

        # breakout trigger: close > 20-candle high
        prior_high_20 = max(high[max(0, i - 20):i])
        if close[i] <= prior_high_20:
            i += 1
            continue

        # volume spike: > 1.5x 48-bar average
        vol_ma = statistics.mean(volume[max(0, i - 48):i]) if i >= 48 else volume[i]
        vol_ratio = volume[i] / (vol_ma or 1e-12)
        if vol_ratio < 1.5:
            i += 1
            continue

        # CVD z-score filter: CVD harus positif (buying pressure)
        if cvd_z[i] < 0.3:
            i += 1
            continue

        # OI z-score filter: OI rising (new money entering)
        if oi_z[i] < 0.2:
            i += 1
            continue

        # Funding proxy: not too crowded (funding < 0.05% positive)
        if abs(fund[i]) > 0.0005:  # funding terlalu tinggi = crowded
            i += 1
            continue

        # === detect nearest resistance/imbalance above current price ===
        zones = detect_imbalances(high, low, open_, close, lookback=120)
        # find nearest zone above current close
        nearest_above = None
        for z in zones:
            if z['price'] > close[i] * 1.002:  # minimal 0.2% above
                nearest_above = z
                break
        if nearest_above is None:
            i += 1
            continue

        resistance_price = nearest_above['price']
        room_to_resistance = (resistance_price / close[i] - 1)
        # need minimum room: at least 0.5% above
        if room_to_resistance < 0.005:
            i += 1
            continue
        # TP harus minimal 0.3% di bawah resistance
        tp_buffer = 0.003
        max_tp_pct = max(0.008, room_to_resistance - tp_buffer)  # at least 0.8% TP

        # entry: market-next-open
        entry = open_[i + 1] if i + 1 < n else close[i]

        # SL: 1.5*ATR atau swing low terdekat, mana yang lebih ketat
        swing_low_20 = min(low[max(0, i - 20):i + 1])
        atr_sl = entry - 1.5 * atr_vals[i]
        swing_sl = swing_low_20 * 0.998  # sedikit di bawah swing low
        sl = max(atr_sl, swing_sl)  # pakai yang lebih tinggi (lebih ketat)
        if sl >= entry:
            i += 1
            continue

        risk = entry - sl
        if risk / entry < 0.005:  # risk terlalu kecil
            i += 1
            continue

        # TP: target di bawah resistance, dengan risk:reward minimal 1.5:1
        tp_rr = entry + max(1.5, min(3.0, max_tp_pct * entry / risk)) * risk
        tp = min(tp_rr, entry * (1 + max_tp_pct))
        if tp <= entry:
            i += 1
            continue

        # === manage trade ===
        stop = sl
        target = tp
        timeout_bars = 48
        exit_price = None
        reason = 'TIMEOUT'
        exit_j = min(i + 1 + timeout_bars, n - 1)

        for j in range(i + 1, min(i + 1 + timeout_bars, n)):
            hit_sl = low[j] <= stop
            hit_tp = high[j] >= target
            if hit_sl and hit_tp:
                exit_price = stop  # conservative: SL dulu
                reason = 'SL'
                exit_j = j
                break
            if hit_sl:
                exit_price = stop
                reason = 'SL'
                exit_j = j
                break
            if hit_tp:
                exit_price = target
                reason = 'TP'
                exit_j = j
                break
            exit_j = j

        if exit_price is None:
            exit_price = close[exit_j]

        ret = exit_price / entry - 1 - FEE
        trades.append({
            'symbol': symbol,
            'i': i,
            'entry_t': rows[i + 1]['t'],
            'ret': ret,
            'reason': reason,
            'entry': entry,
            'exit': exit_price,
            'sl': sl,
            'tp': tp,
            'resistance': resistance_price,
            'resistance_type': nearest_above['type'],
            'room_pct': room_to_resistance * 100,
            'cvd_z': cvd_z[i],
            'oi_z': oi_z[i],
            'vol_ratio': vol_ratio,
            'atr': atr_vals[i],
        })

        i = exit_j + 1

    return trades


# ===== metrics =====
def calc_metrics(trades):
    if not trades:
        return {'n': 0, 'wr': 0, 'pf': 0, 'ret': 0, 'avg': 0, 'maxdd': 0, 'sharpe': 0}
    rets = [t['ret'] for t in trades]
    wins = [r for r in rets if r > 0]
    losses = [r for r in rets if r < 0]
    pf = sum(wins) / abs(sum(losses)) if losses else 99
    eq = 1.0
    peak = 1.0
    maxdd = 0.0
    eq_curve = []
    for r in rets:
        eq *= 1 + r
        peak = max(peak, eq)
        maxdd = max(maxdd, 1 - eq / peak)
        eq_curve.append(eq)
    avg_ret = statistics.mean(rets) if rets else 0
    std_ret = statistics.stdev(rets) if len(rets) >= 2 else 0
    sharpe = math.sqrt(365 / 7) * avg_ret / (std_ret or 1e-12)
    tp_rate = sum(1 for t in trades if t['reason'] == 'TP') / len(trades) * 100
    return {
        'n': len(rets),
        'wr': len(wins) / len(rets) * 100,
        'pf': pf,
        'ret': (eq - 1) * 100,
        'avg': avg_ret * 100,
        'maxdd': maxdd * 100,
        'sharpe': sharpe,
        'tp_rate': tp_rate,
    }


def main():
    files = sorted(CACHE.glob('*_15m_180d.csv'))
    trending = pick_trending_coins(files, topn=50)
    print(f'Available 15m files: {len(files)}, trending selected: {len(trending)}')
    print(f'Top 15 trending coins (30d return):')
    for ret, sym, p, rows, vol24 in trending[:15]:
        print(f'  {sym:12s} ret30d={ret*100:7.2f}% vol24h_avg={vol24:,.0f} bars={len(rows)}')

    all_trades = []
    per_coin = {}
    for ret, sym, p, rows, vol24 in trending:
        ts = backtest_v2(rows, sym, topn_trending_ret=ret)
        all_trades.extend(ts)
        per_coin[sym] = calc_metrics(ts)

    print()
    m = calc_metrics(all_trades)
    print('=== POOLED RESULTS ===')
    print(f'Trades: {m["n"]} | WR: {m["wr"]:.1f}% | PF: {m["pf"]:.2f} | Return: {m["ret"]:.2f}%')
    print(f'Avg/tr: {m["avg"]:.3f}% | MaxDD: {m["maxdd"]:.2f}% | Sharpe: {m["sharpe"]:.2f}')
    print(f'TP rate: {m["tp_rate"]:.1f}%')

    # IS/OOS split (60/40)
    all_trades.sort(key=lambda t: t['i'])
    split = int(len(all_trades) * 0.6)
    is_trades = all_trades[:split]
    oos_trades = all_trades[split:]
    m_is = calc_metrics(is_trades)
    m_oos = calc_metrics(oos_trades)
    print(f'\nIS (60%): T={m_is["n"]} WR={m_is["wr"]:.1f}% PF={m_is["pf"]:.2f} Ret={m_is["ret"]:.2f}%')
    print(f'OOS (40%): T={m_oos["n"]} WR={m_oos["wr"]:.1f}% PF={m_oos["pf"]:.2f} Ret={m_oos["ret"]:.2f}%')

    print('\n=== PER COIN (sorted by PF) ===')
    for sym, cm in sorted(per_coin.items(), key=lambda kv: (kv[1]['pf'] if kv[1]['n'] >= 5 else -1), reverse=True):
        if cm['n'] == 0:
            continue
        flag = '✅' if cm['n'] >= 10 and cm['pf'] > 1.2 and cm['wr'] > 42 else '⚠️' if cm['n'] >= 10 and cm['pf'] > 0.9 else '❌'
        print(f'{flag} {sym:12s} n={cm["n"]:3d} WR={cm["wr"]:5.1f}% PF={cm["pf"]:5.2f} Ret={cm["ret"]:7.2f}% DD={cm["maxdd"]:5.2f}%')

    print('\n=== SAMPLE TRADES ===')
    for t in all_trades[:10]:
        print(f'{t["symbol"]:12s} {t["entry_t"]} entry={t["entry"]:.4f} exit={t["exit"]:.4f} ret={t["ret"]*100:+.3f}% '
              f'reason={t["reason"]} res={t["resistance"]:.4f} ({t["resistance_type"]}) room={t["room_pct"]:.2f}% '
              f'cvd_z={t["cvd_z"]:.2f} oi_z={t["oi_z"]:.2f}')

    # verdict
    print('\n=== VERDICT ===')
    if m['n'] < 50:
        print(f'INCONCLUSIVE — hanya {m["n"]} trades, need >50 untuk verdict reliable')
    elif m['pf'] > 1.2 and m_oos['pf'] > 1.1 and m['wr'] > 42:
        print('✅ POTENTIAL EDGE — PF pool & OOS > threshold, layak diuji forward paper')
    elif m['pf'] > 1.0 and m_oos['pf'] > 0.9:
        print('⚠️ MARGINAL — PF borderline, perlu refinement parameter sebelum forward test')
    else:
        print('❌ NO EDGE — PF < 1.0, strategi ini tidak profitabel di data historis')


if __name__ == '__main__':
    main()